/* Presentation-only desktop integration; no access to Node in the renderer. */
(() => {
  const bridge = window.videoMagicMix;
  const controls = document.getElementById('windowControls');
  if (!controls || typeof bridge?.windowAction !== 'function') return;
  controls.hidden = false;
  document.body.classList.add('desktop-window');
  const maximizeButton = controls.querySelector('[data-window-action="toggle-maximize"]');
  function showState(state) {
    if (!state) return;
    const label = state.maximized ? '还原' : '最大化';
    maximizeButton.title = label;
    maximizeButton.setAttribute('aria-label', label + '窗口');
    maximizeButton.setAttribute('aria-pressed', String(state.maximized));
    document.body.classList.toggle('window-inactive', !state.focused);
  }
  function reportError(error) {
    console.error('窗口操作失败', error);
    window.alert('窗口操作失败：' + error.message);
  }
  controls.querySelectorAll('[data-window-action]').forEach(button => {
    button.addEventListener('click', async () => {
      try { showState(await bridge.windowAction(button.dataset.windowAction)); }
      catch (error) { reportError(error); }
    });
  });
  const unsubscribe = bridge.onWindowState(showState);
  window.addEventListener('pagehide', unsubscribe, { once: true });
  bridge.windowAction('state').then(showState).catch(reportError);
})();
