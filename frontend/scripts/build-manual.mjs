// READMEのユーザー向け範囲から、アプリ内の「使い方」を組み立てる。
// 二重管理をやめるための生成器。READMEが原本で、manual.html は生成物。
//
//   node scripts/build-manual.mjs          生成して書き出す（npm run build の前段）
//   node scripts/build-manual.mjs --check  生成結果と食い違っていたら失敗する（CI用）
//
// 解釈できない記法に出会ったら黙って崩さず、その行を示して失敗する。
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const README = join(here, '..', '..', 'README.md');
const OUTPUT = join(here, '..', 'public', 'manual.html');
const START = '<!-- manual:start -->';
const END = '<!-- manual:end -->';

const STYLE = String.raw`body{font:16px/1.8 'Yu Gothic UI',sans-serif;background:#191320;color:#f5effc;max-width:850px;padding:24px;margin:auto}h1,h2{color:#ddc3ff;line-height:1.4}h1{font-size:26px}h2{font-size:21px;margin-top:36px}code{background:#342540;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}td,th{border:1px solid #665070;padding:8px;text-align:left}a{color:#d5b5ff}.note{padding:14px;background:#30203b;border-left:4px solid #c69bed}li{margin:8px 0}@media(max-width:450px){body{padding:12px;font-size:14px}td,th{padding:6px}}`;
const HEAD = [
  '<!doctype html>',
  '<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
  '<title>かぐやAI 操作マニュアル</title>',
  '<style>',
  STYLE,
  '</style>',
  '<h1>かぐやAI 操作マニュアル</h1>',
  '<p>この内容は README から自動生成しています。編集は README 側で行ってください。</p>',
].join('\n');

function fail(lineNumber, line, reason) {
  console.error(`README.md:${lineNumber} ${reason}`);
  console.error(`  ${line}`);
  console.error('この記法は「使い方」へ変換できません。READMEを直すか、生成器へ対応を足してください。');
  process.exit(1);
}

const escape = value => value
  .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');

// 太字とインラインコード。コードの中身は記号をそのまま見せたいので先に取り出す。
function inline(value) {
  const codes = [];
  let text = value.replace(/`([^`]+)`/g, (_, code) => `\u0000${codes.push(code) - 1}\u0000`);
  text = escape(text).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => `<code>${escape(codes[Number(index)])}</code>`);
}

function collect(markdown) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const picked = [];
  let inside = false;
  lines.forEach((line, index) => {
    const trimmed = line.trim();
    if (trimmed === START) {
      if (inside) fail(index + 1, line, 'manual:start が入れ子になっています。');
      inside = true;
      return;
    }
    if (trimmed === END) {
      if (!inside) fail(index + 1, line, 'manual:end に対応する manual:start がありません。');
      inside = false;
      return;
    }
    if (inside) picked.push({ text: line, at: index + 1 });
  });
  if (inside) fail(lines.length, '', 'manual:end が閉じられていません。');
  if (!picked.length) fail(1, '', 'manual:start 〜 manual:end の範囲が1つもありません。');
  return picked;
}

function render(picked) {
  const out = [];
  let list = null;
  let table = null;
  let paragraph = null;

  const closeList = () => { if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; } };
  const closeTable = () => { if (table) { out.push(`<table>${table.join('')}</table>`); table = null; } };
  const closeParagraph = () => { if (paragraph) { out.push(`<p>${inline(paragraph)}</p>`); paragraph = null; } };
  const closeAll = () => { closeList(); closeTable(); closeParagraph(); };

  for (let index = 0; index < picked.length; index += 1) {
    const { text, at } = picked[index];
    const line = text.replace(/\s+$/, '');

    if (!line.trim()) { closeAll(); continue; }

    if (line.startsWith('```')) {
      closeAll();
      const body = [];
      index += 1;
      while (index < picked.length && !picked[index].text.startsWith('```')) {
        body.push(picked[index].text);
        index += 1;
      }
      if (index >= picked.length) fail(at, line, 'コードブロックが閉じられていません。');
      out.push(`<pre><code>${escape(body.join('\n'))}</code></pre>`);
      continue;
    }

    if (line === '---') { closeAll(); continue; }

    const heading = /^(#{1,3}) (.+)$/.exec(line);
    if (heading) {
      closeAll();
      // READMEのh1が「使い方」の章にあたる。h2以下はそのまま1段下げる。
      const level = Math.min(heading[1].length + 1, 4);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }

    if (line.startsWith('|')) {
      closeList(); closeParagraph();
      const cells = line.split('|').slice(1, -1).map(cell => cell.trim());
      if (cells.every(cell => /^:?-{2,}:?$/.test(cell))) continue;  // 区切り行
      const tag = table ? 'td' : 'th';
      table = table ?? [];
      table.push(`<tr>${cells.map(cell => `<${tag}>${inline(cell)}</${tag}>`).join('')}</tr>`);
      continue;
    }

    if (line.startsWith('- ')) {
      closeTable(); closeParagraph();
      list = list ?? [];
      list.push(`<li>${inline(line.slice(2))}</li>`);
      continue;
    }

    if (/^\s+\S/.test(line) && list) {
      // 箇条書きの折り返し行。直前の項目へ続ける。
      list[list.length - 1] = list[list.length - 1].replace(/<\/li>$/, ` ${inline(line.trim())}</li>`);
      continue;
    }

    if (/^(\d+\.|>|\t)/.test(line)) fail(at, line, '未対応の記法です。');

    closeList(); closeTable();
    paragraph = paragraph ? `${paragraph}${line.trim()}` : line.trim();
  }
  closeAll();
  return out.join('\n');
}

const html = `${HEAD}\n${render(collect(readFileSync(README, 'utf8')))}\n`;

let current = '';
try { current = readFileSync(OUTPUT, 'utf8'); } catch { current = ''; }

// .gitattributes の eol=lf に合わせ、出力は常にLFにする。
// 入力のCRLFはcollectで正規化し、コードブロックにもCRを残さない。
const sameText = (left, right) => left.replace(/\r\n/g, '\n') === right.replace(/\r\n/g, '\n');

if (process.argv.includes('--check')) {
  if (!sameText(current, html)) {
    console.error('public/manual.html が README と食い違っています。');
    console.error('`npm run build`（または node scripts/build-manual.mjs）で作り直して、生成物もコミットしてください。');
    process.exit(1);
  }
  console.log('manual.html は README と一致しています。');
} else {
  writeFileSync(OUTPUT, html, 'utf8');
  console.log(`generated ${OUTPUT}`);
}
