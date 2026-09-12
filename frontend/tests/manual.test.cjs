const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const markdown = '<!-- manual:start -->\n# Guide\n```text\nfirst\nsecond\n```\n<!-- manual:end -->\n';
const manual = 'frontend/public/manual.html';

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'kaguya-manual-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(dir, 'frontend/scripts'), { recursive: true });
  fs.mkdirSync(path.join(dir, 'frontend/public'));
  fs.copyFileSync(path.join(root, 'frontend/scripts/build-manual.mjs'),
    path.join(dir, 'frontend/scripts/build-manual.mjs'));
  return dir;
}

function run(dir, command, args, expected = 0) {
  const result = spawnSync(command, args, {
    cwd: dir, encoding: 'utf8',
    env: { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null',
      GIT_TERMINAL_PROMPT: '0' },
  });
  assert.equal(result.status, expected, `${command}: ${result.error || ''}\n${result.stdout}\n${result.stderr}`);
  return result.stdout;
}
const generate = (dir, ...args) => run(dir, process.execPath, ['frontend/scripts/build-manual.mjs', ...args]);
const git = (dir, ...args) => run(dir, 'git', ['-c', 'core.autocrlf=true', ...args]);

test('LF and CRLF README produce identical LF output, including fenced code', t => {
  const dir = fixture(t);
  fs.writeFileSync(path.join(dir, 'README.md'), markdown);
  generate(dir);
  const expected = fs.readFileSync(path.join(dir, manual), 'utf8');
  assert.ok(expected.includes('<pre><code>first\nsecond</code></pre>'));
  fs.writeFileSync(path.join(dir, 'README.md'), markdown.replaceAll('\n', '\r\n'));
  // Repair the old generator's CRCRLF and lone-CR output as well.
  fs.writeFileSync(path.join(dir, manual), expected.replaceAll('\n', '\r\r\n'));
  generate(dir);
  assert.equal(fs.readFileSync(path.join(dir, manual), 'utf8'), expected);
  generate(dir);
  assert.equal(fs.readFileSync(path.join(dir, manual), 'utf8'), expected);
  generate(dir, '--check');
  fs.appendFileSync(path.join(dir, manual), 'different content');
  run(dir, process.execPath, ['frontend/scripts/build-manual.mjs', '--check'], 1);
});

test('Windows-style build leaves Git clean and permits the next fast-forward update', t => {
  const dir = fixture(t);
  git(dir, 'init', '-b', 'main');
  git(dir, 'config', 'user.name', 'Test');
  git(dir, 'config', 'user.email', 'test@example.invalid');
  fs.writeFileSync(path.join(dir, '.gitattributes'), `${manual} text eol=lf\n`);
  fs.writeFileSync(path.join(dir, 'README.md'), markdown.replaceAll('\n', '\r\n'));
  generate(dir);
  git(dir, 'add', '.');
  git(dir, 'commit', '-m', 'initial');
  const upstream = path.join(dir, 'upstream');
  git(dir, 'clone', '.', upstream);
  git(upstream, 'config', 'user.name', 'Test');
  git(upstream, 'config', 'user.email', 'test@example.invalid');
  fs.writeFileSync(path.join(upstream, 'README.md'), markdown.replace('Guide', 'Updated guide'));
  generate(upstream);
  git(upstream, 'add', '.');
  git(upstream, 'commit', '-m', 'update manual');
  generate(dir);
  assert.equal(git(dir, 'status', '--porcelain', '--untracked-files=no').trim(), '');
  git(dir, 'pull', '--ff-only', upstream, 'main');
  generate(dir);
  assert.equal(git(dir, 'status', '--porcelain', '--untracked-files=no').trim(), '');
  assert.ok(fs.readFileSync(path.join(dir, manual), 'utf8').includes('Updated guide'));
});

test('Windows updater separates fetch errors from merge errors and protects edits',
  { skip: process.platform !== 'win32' }, t => {
    const dir = fixture(t);
    fs.mkdirSync(path.join(dir, 'tools'));
    fs.copyFileSync(path.join(root, 'tools/update_repo.bat'), path.join(dir, 'tools/update_repo.bat'));
    git(dir, 'init', '-b', 'main');
    git(dir, 'config', 'user.name', 'Test');
    git(dir, 'config', 'user.email', 'test@example.invalid');
    fs.writeFileSync(path.join(dir, '.gitignore'), 'upstream/\n');
    fs.writeFileSync(path.join(dir, 'data.txt'), 'initial');
    git(dir, 'add', '.');
    git(dir, 'commit', '-m', 'initial');
    const upstream = path.join(dir, 'upstream');
    git(dir, 'clone', '.', upstream);
    git(upstream, 'config', 'user.name', 'Test');
    git(upstream, 'config', 'user.email', 'test@example.invalid');
    git(dir, 'remote', 'add', 'origin', upstream);
    const update = code => run(dir, 'cmd.exe', ['/d', '/c', 'tools\\update_repo.bat'], code);
    update(0);
    fs.writeFileSync(path.join(upstream, 'data.txt'), 'remote update');
    git(upstream, 'commit', '-am', 'remote change');
    update(5);
    fs.writeFileSync(path.join(dir, 'data.txt'), 'local edit');
    update(10);
    assert.equal(fs.readFileSync(path.join(dir, 'data.txt'), 'utf8'), 'local edit');
    git(dir, 'add', 'data.txt');
    update(10);
    assert.equal(fs.readFileSync(path.join(dir, 'data.txt'), 'utf8'), 'local edit');
    git(dir, 'commit', '-am', 'local commit');
    fs.writeFileSync(path.join(upstream, 'data.txt'), 'divergent update');
    git(upstream, 'commit', '-am', 'divergent change');
    assert.match(update(21), /Fetch succeeded, but the local merge failed/);
    git(dir, 'remote', 'set-url', 'origin', path.join(dir, 'missing-repository'));
    assert.match(update(20), /Could not fetch/);
  });
