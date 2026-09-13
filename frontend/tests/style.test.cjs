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

/** :root で決めた値。var(--x) を実数に直すために読む。 */
const tokens = Object.fromEntries(
  [...block(':root').matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+)/g)].map(m => [m[1], m[2].trim()]));

function resolve(value) {
  return (value ?? '').replace(/var\((--[a-z0-9-]+)\)/g, (_, name) => tokens[name] ?? '');
}

/** padding の短縮形を上右下左に開く。 */
function sides(value) {
  const parts = resolve(value).trim().split(/\s+/).map(part => Number.parseFloat(part) || 0);
  if (parts.length === 1) return [parts[0], parts[0], parts[0], parts[0]];
  if (parts.length === 2) return [parts[0], parts[1], parts[0], parts[1]];
  if (parts.length === 3) return [parts[0], parts[1], parts[2], parts[1]];
  return parts;
}

/** フォーカスの枠が要素の外側へ出る量。 */
function ringReach() {
  const width = Number.parseFloat(resolve(declaration('select:focus-visible', 'outline')));
  const offset = Number.parseFloat(resolve(declaration('select:focus-visible', 'outline-offset')));
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

test('余白・角丸・文字は、決めたトークンだけを使う', () => {
  // 同じ役割のものが場所ごとに違う値になるのを止める。実際、余白は2〜14px、
  // 角丸は8〜18pxが混在していた。
  const polish = fs.readFileSync(path.join(__dirname, '../src/ui-polish.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');
  const both = css + polish;
  // :root の定義そのものは対象外。ここだけが生の値を持つ。
  const body = both.slice(both.indexOf('}', both.indexOf(':root')) + 1);
  const raw = [];
  for (const found of body.matchAll(/(?:^|[;{])\s*(gap|border-radius|font-size)\s*:\s*([^;}]+)/gm)) {
    const [, property, value] = found;
    // env() を使う安全領域の指定と、0 は素の値でよい。
    if (/var\(--|env\(|^0$/.test(value.trim())) continue;
    raw.push(`${property}: ${value.trim()}`);
  }
  // 理由のある例外だけを許す。増やすときは、なぜトークンで足りないのかを
  // CSS側のコメントに書いてから、ここへ足す。
  const allowed = new Set([
    // iOSは入力欄の文字が16px未満だとページを拡大する。設定値にも追従させない。
    'font-size: 16px',
    // 吹き出しは狭い画面・キーボード表示中に、文字サイズの設定と無関係に
    // 収めきる必要がある。段階ではなく実寸で抑える。
    'font-size: 14px', 'font-size: 13px', 'font-size: 12px',
  ]);
  assert.deepEqual(raw.filter(item => !allowed.has(item)), [], '理由の無い生の値が残っている');
});

test('押せるものの最小サイズは、入力手段で分けない', () => {
  // 指で押し間違えないための下限だが、マウスでも小さい的は狙いにくい。
  // メディアクエリの中に置くと、その条件の端末でしか効かない。
  assert.match(css, /--tap:\s*44px/);
  const at = css.search(/min-height:\s*var\(--tap\)/);
  assert.notEqual(at, -1, '最小サイズの指定が見つからない');
  // その位置までの波括弧の深さが1なら、通常のルールの中＝条件なしで効く。
  const head = css.slice(0, at);
  const depth = (head.match(/\{/g) ?? []).length - (head.match(/\}/g) ?? []).length;
  assert.equal(depth, 1, 'メディアクエリの中に入っている');
});
