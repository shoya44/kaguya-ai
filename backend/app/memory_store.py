"""Transactional memory operations, called only by the internal memory API."""
import json
import hashlib
import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

PERSONA_KEYS = ('reply_style', 'addressing', 'support_style')


class WisdomItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    topic_key: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=400)
    kind: Literal['explicit', 'inferred']
    importance: int = Field(ge=1, le=5)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=30)


class WisdomBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    items: list[WisdomItem] = Field(max_length=8)


class PersonaCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: Literal['reply_style', 'addressing', 'support_style']
    value: str = Field(min_length=1, max_length=300)
    source_wisdom_ids: list[UUID] = Field(min_length=1, max_length=5)


def lock(conn):
    conn.execute('SELECT pg_advisory_xact_lock(8765001)')


# ユーザー発言だけでは「わかった」等が何への返事か分からず、要約を誤りやすい。
# かぐや側の回答を短く添えて文脈を補う（長文の回答で予算を食い潰さない長さ）。
REPLY_HINT_CHARS = 160


def snapshot(conn):
    rows = conn.execute("""SELECT id,turn_id,content,status,revision,created_at FROM raw_memory
        WHERE role='user' AND processed_at IS NULL AND status <> 'pending'
        ORDER BY created_at,id LIMIT 30""").fetchall()
    replies = {}
    if rows:
        replies = {row['turn_id']: row['content'] for row in conn.execute(
            """SELECT turn_id,content FROM raw_memory WHERE role='assistant' AND turn_id=ANY(%s)""",
            ([row['turn_id'] for row in rows],)).fetchall()}
    chosen, size = [], 0
    for row in rows:
        row['reply'] = replies.get(row['turn_id'], '')[:REPLY_HINT_CHARS]
        cost = len(row['content']) + len(row['reply']) + 140
        if chosen and size + cost > 6500:
            break
        chosen.append(row)
        size += cost
    wisdom = conn.execute('SELECT * FROM wisdom ORDER BY updated_at DESC LIMIT 8').fetchall()
    # Bound the prompt, not the database. Missing update targets cause a conflict.
    return {'raw': chosen, 'wisdom': wisdom}


def validate_batch(batch, snap):
    result = WisdomBatch.model_validate(batch)
    rows = {str(row['id']): row for row in snap['raw'] if row['status'] != 'cancelled'}
    topics = set()
    for item in result.items:
        if not item.topic_key.strip() or not item.summary.strip() or item.topic_key in topics:
            raise ValueError('invalid or duplicate topic')
        topics.add(item.topic_key)
        if any(str(raw_id) not in rows for raw_id in item.evidence_ids):
            raise ValueError('evidence must reference selected user messages')
    return result


def commit_wisdom(conn, snap, batch):
    result = validate_batch(batch, snap)
    lock(conn)
    raw = {str(row['id']): row for row in snap['raw']}
    if not raw:
        return {'processed': 0, 'updated': 0}
    actual = conn.execute('SELECT * FROM raw_memory WHERE id=ANY(%s) FOR UPDATE',
                          ([UUID(key) for key in raw],)).fetchall()
    if len(actual) != len(raw) or any(row['processed_at'] is not None or
            row['revision'] != raw[str(row['id'])]['revision'] or row['status'] == 'pending' or
            row['content'] != raw[str(row['id'])]['content'] for row in actual):
        raise HTTPException(409, 'memory_changed')
    revisions = {row['topic_key']: row['revision'] for row in snap['wisdom']}
    supported = set()
    for item in result.items:
        old = conn.execute('SELECT * FROM wisdom WHERE topic_key=%s FOR UPDATE', (item.topic_key,)).fetchone()
        if (old and (old['revision'] != revisions.get(item.topic_key) or old['locked'])) or (
                not old and item.topic_key in revisions):
            raise HTTPException(409, 'memory_changed')
        evidence = {entry['raw_id']: entry for entry in old['evidence']} if old else {}
        for raw_id in item.evidence_ids:
            row = raw[str(raw_id)]
            supported.add(str(raw_id))
            stamp = row['created_at'] if isinstance(row['created_at'], str) else row['created_at'].isoformat()
            from .proactive import JST
            day = datetime.fromisoformat(stamp).astimezone(JST).date().isoformat()
            evidence[str(raw_id)] = {'raw_id': str(raw_id), 'date': day, 'excerpt': row['content'][:120]}
        support = 'unconfirmed' if item.kind == 'inferred' else (
            'repeated' if len({e['date'] for e in evidence.values()}) >= 2 else 'stated')
        last_seen = max(entry['date'] for entry in evidence.values()) + 'T00:00:00+09:00'
        value = (item.summary, item.kind, support, item.importance, Jsonb(list(evidence.values())))
        if old:
            conn.execute('''UPDATE wisdom SET summary=%s,kind=%s,support_level=%s,importance=%s,evidence=%s,
                last_seen_at=%s,revision=revision+1,updated_at=now() WHERE id=%s''', (*value, last_seen, old['id']))
        else:
            conn.execute('''INSERT INTO wisdom (id,topic_key,summary,kind,support_level,importance,evidence,last_seen_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''', (uuid4(), item.topic_key, *value, last_seen))
    for row in actual:
        reason = 'cancelled' if row['status'] == 'cancelled' else (
            'wisdom' if str(row['id']) in supported else 'no_durable_fact')
        conn.execute('''UPDATE raw_memory SET processed_at=now(),processing_reason=%s
            WHERE turn_id=%s AND status <> 'pending' ''', (reason, row['turn_id']))
    return {'processed': len(actual), 'updated': len(result.items)}


def recall(conn, text):
    # Japanese partial matching without another service or tokenizer.
    chunks = re.findall(r'[一-龯ぁ-んァ-ヶーA-Za-z0-9]{2,}', text)
    terms = set(chunks)
    for chunk in chunks:
        terms.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    # 80語で打ち切る際、単純なsortだと文字コード順（英数→かな→漢字）になり、
    # いちばん手がかりになる漢字語から捨ててしまう。長い語＝具体的な語を優先する。
    patterns = ['%' + term + '%' for term in sorted(terms, key=lambda term: (-len(term), term))[:80]]
    rows = conn.execute('''SELECT * FROM wisdom WHERE topic_key LIKE ANY(%s) OR summary LIKE ANY(%s)
        ORDER BY importance DESC,last_seen_at DESC NULLS LAST LIMIT 30''', (patterns, patterns)).fetchall() if patterns else []

    def relevance(row):
        # 一致語数だけで並べると、長い要約が偶然当たって上位に来る。重要度と、
        # 明示的に語られた知恵（推測ではない）を加点して順位に反映させる。
        hits = sum(term in row['topic_key'] or term in row['summary'] for term in terms)
        return hits * 2 + row['importance'] + (1 if row['kind'] == 'explicit' else 0)

    rows.sort(key=relevance, reverse=True)
    selected = rows[:5]
    if selected:
        conn.execute('UPDATE wisdom SET last_used_at=now() WHERE id=ANY(%s)', ([row['id'] for row in selected],))
    persona = conn.execute("SELECT * FROM persona ORDER BY key LIMIT 12").fetchall()
    return {'wisdom': selected, 'persona': persona}


def weekly_snapshot(conn):
    wisdom = conn.execute("SELECT * FROM wisdom WHERE kind='explicit' AND support_level='repeated' ORDER BY updated_at DESC LIMIT 50").fetchall()
    wisdom = [row for row in wisdom if len({entry['date'] for entry in row['evidence']}) >= 3][:5]
    persona = conn.execute('SELECT * FROM persona WHERE key=ANY(%s) AND NOT locked', (list(PERSONA_KEYS),)).fetchall()
    newest = max((row['updated_at'] for row in wisdom), default=None)
    if not newest or not any(row['updated_at'] < newest for row in persona):
        wisdom = []
    return {'wisdom': wisdom, 'persona': persona}


def commit_persona(conn, snap, candidate):
    item = PersonaCandidate.model_validate(candidate)
    known = {str(row['id']): row for row in snap['wisdom']}
    sources = [str(source) for source in item.source_wisdom_ids]
    if any(source not in known or known[source]['kind'] != 'explicit' or
            len({e['date'] for e in known[source]['evidence']}) < 3 for source in sources):
        raise ValueError('insufficient evidence')
    lock(conn)
    for source in sources:
        current = conn.execute('SELECT revision FROM wisdom WHERE id=%s', (UUID(source),)).fetchone()
        if not current or current['revision'] != known[source]['revision']:
            raise HTTPException(409, 'memory_changed')
    old = conn.execute('SELECT * FROM persona WHERE key=%s FOR UPDATE', (item.key,)).fetchone()
    expected = next((row for row in snap['persona'] if row['key'] == item.key), None)
    if not old or old['locked'] or not expected or old['revision'] != expected['revision']:
        raise HTTPException(409, 'memory_changed')
    conn.execute('''UPDATE persona SET previous_value=value, previous_source_wisdom_ids=source_wisdom_ids,
        value=%s,source_wisdom_ids=%s,revision=revision+1,updated_at=now() WHERE key=%s''',
                 (Jsonb(item.value), Jsonb(sources), item.key))
    return {'updated': 1}


def list_memories(conn, layer, query='', offset=0):
    if layer == 'raw':
        rows = conn.execute('''SELECT * FROM raw_memory WHERE content ILIKE %s
            ORDER BY created_at DESC,id DESC LIMIT 31 OFFSET %s''', ('%' + query + '%', offset)).fetchall()
    elif layer == 'wisdom':
        rows = conn.execute('''SELECT * FROM wisdom WHERE summary ILIKE %s OR topic_key ILIKE %s
            ORDER BY updated_at DESC,id DESC LIMIT 31 OFFSET %s''', ('%' + query + '%', '%' + query + '%', offset)).fetchall()
    elif layer == 'persona':
        rows = conn.execute('SELECT * FROM persona ORDER BY key LIMIT 31 OFFSET %s', (offset,)).fetchall()
    else:
        raise HTTPException(404, 'Unknown layer')
    return {'items': rows[:30], 'next_offset': offset + 30 if len(rows) > 30 else None}


def impact(conn, layer, key):
    if layer == 'raw':
        selected = conn.execute('SELECT * FROM raw_memory WHERE id=%s', (UUID(key),)).fetchone()
    elif layer == 'wisdom':
        selected = conn.execute('SELECT * FROM wisdom WHERE id=%s', (UUID(key),)).fetchone()
    elif layer == 'persona' and key in PERSONA_KEYS:
        selected = conn.execute('SELECT * FROM persona WHERE key=%s', (key,)).fetchone()
    else:
        raise HTTPException(400, '固定性格は変更できません。')
    if not selected:
        raise HTTPException(404, '記憶が見つかりません。')
    all_wisdom = conn.execute('SELECT * FROM wisdom').fetchall()
    all_persona = conn.execute('SELECT * FROM persona').fetchall()
    raw_ids, wisdom_ids, persona_keys = set(), set(), set()
    if layer == 'raw':
        paired = conn.execute('SELECT id FROM raw_memory WHERE turn_id=%s', (selected['turn_id'],)).fetchall()
        raw_ids.update(str(row['id']) for row in paired)
    elif layer == 'wisdom':
        wisdom_ids.add(str(selected['id']))
        raw_ids.update(e['raw_id'] for e in selected['evidence'])
    else:
        persona_keys.add(key)
        wisdom_ids.update(selected['source_wisdom_ids'] + selected['previous_source_wisdom_ids'])
        for row in all_wisdom:
            if str(row['id']) in wisdom_ids:
                raw_ids.update(e['raw_id'] for e in row['evidence'])
    # Remove shared derivations conservatively, including rollback sources.
    for row in all_wisdom:
        if any(e['raw_id'] in raw_ids for e in row['evidence']):
            wisdom_ids.add(str(row['id']))
    for row in all_persona:
        if set(row['source_wisdom_ids'] + row['previous_source_wisdom_ids']) & wisdom_ids:
            persona_keys.add(row['key'])
    turns = conn.execute('SELECT DISTINCT turn_id FROM raw_memory WHERE id=ANY(%s)',
                         ([UUID(key) for key in raw_ids],)).fetchall() if raw_ids else []
    raw_turns = [row['turn_id'] for row in turns]
    affected_raw = conn.execute('SELECT id,revision FROM raw_memory WHERE turn_id=ANY(%s)', (raw_turns,)).fetchall() if raw_turns else []
    versions = {'selected': selected['revision'],
                'raw': sorted((str(row['id']), row['revision']) for row in affected_raw),
                'wisdom': sorted((str(row['id']), row['revision']) for row in all_wisdom if str(row['id']) in wisdom_ids),
                'persona': sorted((row['key'], row['revision']) for row in all_persona if row['key'] in persona_keys)}
    token = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()
    return {'selected': selected, 'raw_turns': raw_turns, 'impact_token': token,
            'wisdom_ids': sorted(wisdom_ids), 'persona_keys': sorted(persona_keys)}


def mutate(conn, layer, key, body, delete=False):
    lock(conn)
    affected = impact(conn, layer, key)
    selected = affected['selected']
    if selected['revision'] != body['revision'] or (
            body.get('impact_token') and body['impact_token'] != affected['impact_token']):
        raise HTTPException(409, 'memory_changed')
    value = body.get('value', '').strip()
    if not delete and (not value or len(value) > (2000 if layer == 'raw' else 400)):
        raise HTTPException(400, '入力文字数を確認してください。')
    if not delete and layer == 'raw' and selected['role'] != 'user':
        raise HTTPException(400, 'AIの回答は訂正できません。会話全体の削除はできます。')
    for persona_key in affected['persona_keys']:
        if persona_key not in PERSONA_KEYS:
            continue
        conn.execute('''UPDATE persona SET value=%s,source_wisdom_ids='[]',previous_value=NULL,
            previous_source_wisdom_ids='[]',revision=revision+1,updated_at=now()
            WHERE key=%s''', (Jsonb('現在のユーザーの要望を優先する。'), persona_key))
    if affected['wisdom_ids']:
        conn.execute('DELETE FROM wisdom WHERE id=ANY(%s)', ([UUID(key) for key in affected['wisdom_ids']],))
    if affected['raw_turns']:
        if layer == 'raw' and not delete:
            conn.execute("DELETE FROM raw_memory WHERE turn_id=ANY(%s) AND role='assistant'", (affected['raw_turns'],))
        else:
            conn.execute('DELETE FROM raw_memory WHERE turn_id=ANY(%s)', (affected['raw_turns'],))
    if not delete:
        if layer == 'raw':
            conn.execute("""UPDATE raw_memory SET content=%s,status='failed',processed_at=NULL,
                processing_reason=NULL,revision=revision+1 WHERE id=%s""", (value, selected['id']))
        elif layer == 'wisdom':
            conn.execute('''INSERT INTO wisdom (id,topic_key,summary,kind,support_level,importance,evidence,locked,revision,last_seen_at)
                VALUES (%s,%s,%s,'explicit','stated',%s,'[]',true,%s,now())''',
                         (selected['id'], selected['topic_key'], value, selected['importance'], selected['revision'] + 1))
        else:
            conn.execute('''UPDATE persona SET value=%s,locked=%s,source_wisdom_ids='[]',
                previous_value=NULL,previous_source_wisdom_ids='[]',revision=revision+1,updated_at=now() WHERE key=%s''',
                         (Jsonb(value), body.get('locked', True), key))
    return {'ok': True}


def restore_persona(conn, key, revision):
    if key not in PERSONA_KEYS:
        raise HTTPException(400, '固定性格は変更できません。')
    lock(conn)
    row = conn.execute('SELECT * FROM persona WHERE key=%s FOR UPDATE', (key,)).fetchone()
    if not row or row['revision'] != revision or row['previous_value'] is None:
        raise HTTPException(409, 'memory_changed')
    conn.execute('''UPDATE persona SET value=previous_value,source_wisdom_ids=previous_source_wisdom_ids,
        previous_value=NULL,previous_source_wisdom_ids='[]',locked=true,revision=revision+1,updated_at=now() WHERE key=%s''', (key,))
    return {'ok': True}


def add_reminder(conn, due_at, message):
    conn.execute('INSERT INTO reminders (id,due_at,message) VALUES (%s,%s,%s)',
                 (uuid4(), due_at, message))
    return {'ok': True}


def pending_reminders(conn):
    rows = conn.execute('''SELECT id,due_at,message FROM reminders WHERE delivered_at IS NULL
        ORDER BY due_at LIMIT 50''').fetchall()
    return {'items': rows}


def take_due_reminders(conn, now):
    """期限が来た分を取り出し、同時に配信済みにする（二重配信を防ぐ）。

    アプリが止まっていた間に過ぎた分も、次に起動したときここで拾われる。
    """
    rows = conn.execute('''UPDATE reminders SET delivered_at=now() WHERE id IN (
        SELECT id FROM reminders WHERE delivered_at IS NULL AND due_at <= %s
        ORDER BY due_at LIMIT 5) RETURNING id,due_at,message''', (now,)).fetchall()
    return {'items': rows}


def delete_reminder(conn, reminder_id):
    result = conn.execute('DELETE FROM reminders WHERE id=%s', (UUID(reminder_id),))
    if not result.rowcount:
        raise HTTPException(404, '予約が見つかりません。')
    return {'ok': True}


def remember(conn, topic_key, summary):
    """「覚えておいて」と明示的に頼まれた事実を、日次整理を待たずに保存する。

    自動更新で上書きされないよう locked を立てる（記憶タブから訂正・削除できる）。
    """
    lock(conn)
    old = conn.execute('SELECT id FROM wisdom WHERE topic_key=%s FOR UPDATE', (topic_key,)).fetchone()
    if old:
        conn.execute('''UPDATE wisdom SET summary=%s,kind='explicit',support_level='stated',locked=true,
            last_seen_at=now(),revision=revision+1,updated_at=now() WHERE id=%s''', (summary, old['id']))
    else:
        conn.execute('''INSERT INTO wisdom (id,topic_key,summary,kind,support_level,importance,evidence,locked,last_seen_at)
            VALUES (%s,%s,%s,'explicit','stated',4,'[]',true,now())''', (uuid4(), topic_key, summary))
    return {'ok': True}


def cleanup(conn):
    lock(conn)
    # Keep complete pairs and never discard an unprocessed partner.
    result = conn.execute("""DELETE FROM raw_memory WHERE turn_id IN (
        SELECT turn_id FROM raw_memory GROUP BY turn_id
        HAVING bool_and(processed_at IS NOT NULL AND created_at < now()-interval '7 days'))""")
    return {'removed': result.rowcount}
