const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');

// コメントにもセレクタ名が出てくる（説明に使っている）ので、先に落とす。
const css = fs.readFileSync(path.join(__dirname, '../src/style.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');

/** セレクタを含むブロックの中身を返す。まとめ書き（`a, b { }`）にも当たる。 */
function block(selector) {
  const at = css.indexOf(selector);
  assert.notEqual(at, -1, `${selector} が見つからない`);
  const open = css.indexOf('{', at);
  return css.slice(open + 1, css.indexOf('}', open));
}

function declaration(selector, property) {
  // 直前がコメントのこともあるので、行頭からも拾えるようにする。
  const found = new RegExp(`(?:^|[;{])\\s*${property}\\s*:\\s*([^;]+)`, 'm').exec(block(selector));
  return found ? found[1].trim() : null;
}

/** padding の短縮形を上右下左に開く。 */
function sides(value) {
  const parts = (value ?? '').trim().split(/\s+/).map(part => Number.parseFloat(part) || 0);
  if (parts.length === 1) return [parts[0], parts[0], parts[0], parts[0]];
  if (parts.length === 2) return [parts[0], parts[1], parts[0], parts[1]];
  if (parts.length === 3) return [parts[0], parts[1], parts[2], parts[1]];
  return parts;
}

/** フォーカスの枠が要素の外側へ出る量。 */
function ringReach() {
  const width = Number.parseFloat(declaration('select:focus-visible', 'outline'));
  const offset = Number.parseFloat(declaration('select:focus-visible', 'outline-offset'));
  assert.ok(Number.isFinite(width) && Number.isFinite(offset), '枠の太さとずらし量が読めること');
  return width + offset;
}

test('縦にスクロールする画面は、フォーカスの枠が切れない余白を持つ', () => {
  // overflow-y だけ指定しても横が visible のままにはならず、横も切り取られる。
  // 余白が足りないと、画面の縁にある操作の枠が欠ける。実際、記憶タブの
  // 「会話履歴」は左の余白が0で、選んだときの枠が切れていた。
  const needed = ringReach();
  const [top, right, bottom, left] = sides(declaration('.panel', 'padding'));
  for (const [name, gap] of [['上', top], ['右', right], ['下', bottom], ['左', left]]) {
    assert.ok(gap >= needed, `.panel の${name}の余白 ${gap}px が、枠に必要な ${needed}px に足りない`);
  }
});

test('記憶画面の操作は、指で押せる大きさを確保する', () => {
  // Apple HIG の最小 44pt に合わせてある。小さくすると押し間違いが増える。
  const touch = block('#app.mode-normal .panel button,');
  assert.match(touch, /min-height:\s*44px/);
});
