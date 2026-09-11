import { invoke } from '@tauri-apps/api/core';
import { isTauri } from './tauri';

function setupRepositoryUpdateButton(): void {
  if (!isTauri()) return;

  const row = document.querySelector('.toolbar .button-row');
  if (!row || document.getElementById('repo-update-btn')) return;

  const button = document.createElement('button');
  button.id = 'repo-update-btn';
  button.type = 'button';
  button.textContent = '最新版を反映';
  button.setAttribute('aria-label', 'GitHubのmainブランチを最新版に更新して再起動');
  row.prepend(button);

  button.addEventListener('click', () => {
    const confirmed = window.confirm(
      'GitHubのmainブランチから最新版を取得して、かぐやAIを再起動します。続けますか？',
    );
    if (!confirmed) return;

    button.disabled = true;
    button.textContent = '更新して再起動中…';
    invoke('restart_with_update').catch(() => {
      // 成功時はアプリ自体が終了するため、ここに来るのは主に起動失敗時。
      button.disabled = false;
      button.textContent = '最新版を反映';
      window.alert('更新処理を開始できませんでした。コマンドプロンプトで kaguya.bat update を実行してください。');
    });
  });
}

setupRepositoryUpdateButton();
