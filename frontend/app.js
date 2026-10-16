let project = null;
let selectedSchemeId = null;
let availableFonts = [];
let subtitleStyleTimer = null;
let selectedCueIndex = 0;
const $ = id => document.getElementById(id);
const api = async (url, options = {}) => { const res = await fetch(url, options); const data = await res.json().catch(() => ({})); if (!res.ok) { const detail = data.detail; throw Error(typeof detail === 'string' ? detail : detail ? JSON.stringify(detail) : `请求失败 ${res.status}`); } return data; };
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function log(message, kind = '') { const el = document.createElement('div'); el.className = `log-line ${kind}`; el.textContent = message; $('statusLog').prepend(el); $('logCount').textContent = document.querySelectorAll('.log-line').length; }
function renderMessages() { $('errors').textContent = (project?.errors || []).join('\n'); $('warnings').textContent = (project?.warnings || []).join('\n'); $('errors').style.display = project?.errors?.length ? 'block' : 'none'; $('warnings').style.display = project?.warnings?.length ? 'block' : 'none'; }
function bodyRowsForSelectedScheme() { const scheme = project?.schemes?.find(item => item.scheme_id === ($('schemeSelect').value || selectedSchemeId)); return scheme ? scheme.preview.filter(item => item.row_key.toLowerCase() !== 'hook') : []; }
function bodyConfig(schemeId, rowKey) { return project?.body_configs?.[schemeId]?.[rowKey] || {}; }
function renderBodyPreview() {
  const scheme = project?.schemes?.find(item => item.scheme_id === ($('schemeSelect').value || selectedSchemeId));
  if (!scheme) { $('bodyPreview').innerHTML = '<span class="muted">导入 Excel 后显示当前字幕方案的 Body 文案</span>'; return; }
  selectedSchemeId = scheme.scheme_id;
  $('bodyPreview').innerHTML = bodyRowsForSelectedScheme().map(item => {
    if (!item.enabled) return `<div class="body-preview-row skipped"><div class="body-row-title"><b>${escapeHtml(item.row_key)}</b><span>${escapeHtml(item.group_name || '未命名分组')}</span></div><code>文案为空，已跳过</code></div>`;
    const config = bodyConfig(scheme.scheme_id, item.row_key);
    const metrics = config.scanned_video_count ? `素材 ${config.scanned_video_count} 个 / 可用组合 ${config.actual_candidate_count ?? 0} 个` : '等待配音识别和素材扫描';
    return `<div class="body-preview-row"><div class="body-row-title"><b>${escapeHtml(item.row_key)}</b><span>${escapeHtml(item.group_name || '未命名分组')}</span></div><code>${escapeHtml(item.preview || '')}</code><input class="body-folder-path" value="${escapeHtml(project.body_mapping?.[item.row_key] || '')}" placeholder="请选择该 Body 文件夹" readonly><button class="outline body-folder-button" data-body-folder="${escapeHtml(item.row_key)}">添加</button><button class="outline body-clear-button" data-body-clear="${escapeHtml(item.row_key)}">清除</button><label class="body-inline-field">候选<input type="number" min="1" step="1" value="${escapeHtml(config.candidate_count ?? 1)}" data-body-candidate="${escapeHtml(item.row_key)}"></label><label class="body-inline-field">阈值<input type="number" min="1" step="0.001" value="${escapeHtml(config.threshold ?? 1.15)}" data-body-threshold="${escapeHtml(item.row_key)}"></label><span class="body-recommended">推荐 ${config.recommended_threshold == null ? '--' : Number(config.recommended_threshold).toFixed(3)}</span><span class="body-metrics">${escapeHtml(metrics)}${config.target_duration ? ` / 目标 ${Number(config.target_duration).toFixed(2)}秒` : ''}</span></div>`;
  }).join('') || '<span class="muted">没有 Body 行</span>';
  document.querySelectorAll('[data-body-folder]').forEach(button => button.onclick = () => chooseBodyFolder(button.dataset.bodyFolder));
  document.querySelectorAll('[data-body-clear]').forEach(button => button.onclick = () => clearBodyFolder(button.dataset.bodyClear));
  document.querySelectorAll('[data-body-candidate]').forEach(input => input.onchange = () => updateBodyConfig(scheme.scheme_id, input.dataset.bodyCandidate, 'candidate_count', input.value));
  document.querySelectorAll('[data-body-threshold]').forEach(input => input.onchange = () => updateBodyConfig(scheme.scheme_id, input.dataset.bodyThreshold, 'threshold', input.value));
}
function schemeStatus(scheme) { if (scheme.match_status === 'matched') return '匹配成功'; if (scheme.match_status === 'failed') return `匹配失败：${scheme.error || '请检查配音'}`; if (scheme.asr_status === 'uploaded') return '已上传，等待识别'; return '未上传配音'; }
function renderSchemes() { $('schemePanel').classList.remove('hidden'); $('schemeSelect').disabled = !project?.schemes?.length; $('schemeSelect').innerHTML = (project?.schemes || []).map(s => `<option value="${escapeHtml(s.scheme_id)}">${escapeHtml(s.display_name)} / ${escapeHtml(s.source_column)}</option>`).join(''); if (!selectedSchemeId && project?.schemes?.length) selectedSchemeId = project.schemes[0].scheme_id; $('schemeSelect').value = selectedSchemeId || ''; renderBodyPreview(); renderSubtitleStyle(); $('schemeList').innerHTML = (project?.schemes || []).map(scheme => `<article class="scheme-row"><div><strong>${escapeHtml(scheme.display_name)} / ${escapeHtml(scheme.source_column)}</strong><span class="status ${scheme.match_status === 'failed' ? 'bad' : ''}" data-status="${escapeHtml(scheme.scheme_id)}">${escapeHtml(schemeStatus(scheme))}</span><div class="scheme-preview">${scheme.preview.map(item => `<span>${escapeHtml(item.row_key)}：${escapeHtml(item.enabled ? item.preview : item.skip_reason || '跳过')}</span>`).join('')}</div><div class="scheme-error ${scheme.error ? 'bad' : ''}" data-error="${escapeHtml(scheme.scheme_id)}">${escapeHtml(scheme.error || '')}</div></div><input type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,.opus" data-voice="${escapeHtml(scheme.scheme_id)}"><button type="button" class="outline" data-upload="${escapeHtml(scheme.scheme_id)}">上传并识别</button></article>`).join(''); document.querySelectorAll('[data-upload]').forEach(button => button.onclick = () => uploadVoice(button.dataset.upload)); }
function currentScheme() { return project?.schemes?.find(item => item.scheme_id === ($('schemeSelect').value || selectedSchemeId)); }
function renderSubtitleStyle() {
  const style = project?.subtitle_style || {font_name: availableFonts[0]?.name || 'Microsoft YaHei', font_size: 52, bold: true, primary_color: '#FFFFFF', outline_color: '#000000', outline_width: 3, shadow: 1, alignment: 2, margin_vertical: 120, max_width: 90, max_chars_per_cue: 10};
  if ($('subtitleFont')) $('subtitleFont').value = style.font_name || availableFonts[0]?.name || '';
  if ($('subtitleFontSize')) $('subtitleFontSize').value = style.font_size ?? 52;
  if ($('subtitleBold')) $('subtitleBold').checked = style.bold !== false;
  if ($('subtitlePrimaryColor')) $('subtitlePrimaryColor').value = style.primary_color || '#FFFFFF';
  if ($('subtitleOutlineColor')) $('subtitleOutlineColor').value = style.outline_color || '#000000';
  if ($('subtitleOutlineWidth')) $('subtitleOutlineWidth').value = style.outline_width ?? 3;
  if ($('subtitleShadow')) $('subtitleShadow').value = style.shadow ?? 1;
  if ($('subtitleAlignment')) $('subtitleAlignment').value = style.alignment ?? 2;
  if ($('subtitleMargin')) $('subtitleMargin').value = style.margin_vertical ?? 120;
  if ($('subtitleMaxWidth')) $('subtitleMaxWidth').value = style.max_width ?? 90;
  if ($('subtitleMaxChars')) $('subtitleMaxChars').value = style.max_chars_per_cue ?? 10;
  const scheme = currentScheme();
  const previous = $('subtitlePreviewSegment')?.value;
  if ($('subtitlePreviewSegment')) {
    $('subtitlePreviewSegment').innerHTML = (scheme?.preview || []).filter(item => item.enabled && item.row_key.toLowerCase() !== 'hook').map(item => `<option value="${escapeHtml(item.row_key)}">${escapeHtml(item.row_key)} / ${escapeHtml(item.preview || '')}</option>`).join('');
    if (previous && [...$('subtitlePreviewSegment').options].some(option => option.value === previous)) $('subtitlePreviewSegment').value = previous;
  }
  renderSubtitleCues();
}

function splitPreviewText(text, limit) { const phrases = String(text || '').split(/[\r\n，,、；;。.!！?？]+/).map(value => value.replace(/\s+/g, '')).filter(Boolean); return phrases.flatMap(value => { const out = []; for (let i = 0; i < value.length; i += limit) out.push(value.slice(i, i + limit)); return out; }); }
function currentPreviewCues() { const scheme = currentScheme(); const rowKey = $('subtitlePreviewSegment')?.value; const segment = scheme?.segments?.find(item => item.row_key === rowKey); if (!segment) return []; if (segment.subtitle_cues?.length) return segment.subtitle_cues; return splitPreviewText(segment.text, Number(project?.subtitle_style?.max_chars_per_cue || 10)).map(text => ({text, matched:false})); }
function renderSubtitleCues() { const cues = currentPreviewCues(); if (!cues.length) { $('subtitleCueList').innerHTML = '<span class="muted">等待选择 Body 文案</span>'; return; } selectedCueIndex = Math.min(selectedCueIndex, cues.length - 1); $('subtitleCueList').innerHTML = cues.map((cue, index) => `<button type="button" class="subtitle-cue ${index === selectedCueIndex ? 'active' : ''}" data-cue-index="${index}"><span>${index + 1}. ${escapeHtml(cue.text)}</span><small>${cue.matched ? `${Number(cue.start_seconds).toFixed(2)}–${Number(cue.end_seconds).toFixed(2)} 秒` : '等待识别'}</small></button>`).join(''); document.querySelectorAll('[data-cue-index]').forEach(button => button.onclick = () => { selectedCueIndex = Number(button.dataset.cueIndex); renderSubtitleCues(); refreshSubtitlePreview(); }); }
async function loadFonts() {
  try {
    const result = await api('/api/fonts');
    availableFonts = result.fonts || [];
    $('subtitleFont').innerHTML = availableFonts.map(font => `<option value="${escapeHtml(font.name)}">${escapeHtml(font.name)}${font.source === 'bundled' ? ' / 内置' : ''}${font.supports_chinese ? '' : ' / 可能缺少中文'}</option>`).join('');
    $('fontStats').textContent = `可用字体 ${result.count}，中文标记 ${result.chinese_count}`;
    renderSubtitleStyle();
  } catch (error) { $('fontStats').textContent = '字体列表读取失败'; log(`字体列表读取失败：${error.message}`, 'error'); }
}
function subtitleStyleForm() { const fd = new FormData(); fd.append('font_name', $('subtitleFont').value); fd.append('font_size', $('subtitleFontSize').value); fd.append('bold', $('subtitleBold').checked ? 'true' : 'false'); fd.append('primary_color', $('subtitlePrimaryColor').value); fd.append('outline_color', $('subtitleOutlineColor').value); fd.append('outline_width', $('subtitleOutlineWidth').value); fd.append('shadow', $('subtitleShadow').value); fd.append('alignment', $('subtitleAlignment').value); fd.append('margin_vertical', $('subtitleMargin').value); fd.append('max_width', $('subtitleMaxWidth').value); fd.append('max_chars_per_cue', $('subtitleMaxChars').value); return fd; }
function scheduleSubtitleStyleSave() { clearTimeout(subtitleStyleTimer); subtitleStyleTimer = setTimeout(saveSubtitleStyle, 250); }
async function saveSubtitleStyle() { if (!project) return; try { project = await api(`/api/projects/${project.project_id}/subtitle-style`, { method: 'POST', body: subtitleStyleForm() }); renderSubtitleStyle(); await refreshSubtitlePreview(); } catch (error) { log(`字幕样式保存失败：${error.message}`, 'error'); } }
async function refreshSubtitlePreview() { const image = $('subtitlePreview'); const message = $('subtitlePreviewMessage'); renderSubtitleCues(); if (!project || !currentScheme() || !$('subtitlePreviewSegment').value) { image.classList.add('hidden'); message.textContent = '导入 Excel 并选择 Body 素材后显示真实字幕预览'; return; } const rowKey = $('subtitlePreviewSegment').value; image.classList.add('hidden'); message.textContent = '正在生成真实字幕预览...'; image.onload = () => { image.classList.remove('hidden'); message.textContent = ''; }; image.onerror = () => { image.classList.add('hidden'); message.textContent = '暂无可预览素材，请先选择对应 Body 文件夹'; }; image.src = `/api/projects/${encodeURIComponent(project.project_id)}/subtitle-preview?scheme_id=${encodeURIComponent(currentScheme().scheme_id)}&row_key=${encodeURIComponent(rowKey)}&cue_index=${selectedCueIndex}&t=${Date.now()}`; }
async function waitForTranscription(taskId, id, button) { const clientStarted = Date.now(); let requestFailures = 0; for (;;) { let task; try { task = await api(`/api/tasks/${taskId}`); requestFailures = 0; } catch (error) { requestFailures += 1; if (requestFailures >= 5) throw Error(`无法读取识别进度：${error.message}`); await new Promise(resolve => setTimeout(resolve, 1500)); continue; } const elapsed = task.elapsed_seconds ?? Math.floor((Date.now() - clientStarted) / 1000); button.textContent = task.status === 'running' ? `识别中 ${elapsed}秒` : '等待识别...'; const status = document.querySelector(`[data-status="${id}"]`); if (status) status.textContent = task.message || button.textContent; if (task.status === 'completed' || task.status === 'failed') return task; if (Date.now() - clientStarted > 31 * 60 * 1000) throw Error('识别任务超过 31 分钟且没有结束，请重启软件后重新识别'); await new Promise(resolve => setTimeout(resolve, 1000)); } }
async function uploadVoice(id) { const input = document.querySelector(`[data-voice="${id}"]`); const button = document.querySelector(`[data-upload="${id}"]`); if (!input?.files?.[0]) return alert('请选择配音文件'); const fd = new FormData(); fd.append('file', input.files[0]); button.disabled = true; button.textContent = '上传中...'; try { log(`上传 ${id} 配音并开始 ASR`); await api(`/api/projects/${project.project_id}/schemes/${id}/voice`, { method: 'POST', body: fd }); const task = await api(`/api/projects/${project.project_id}/schemes/${id}/transcribe`, { method: 'POST' }); const result = await waitForTranscription(task.task_id, id, button); const refreshed = await api(`/api/projects/${project.project_id}`); project.schemes = refreshed.schemes; renderSchemes(); if (result.status === 'completed') log(`${id} 字幕匹配完成`, 'success'); else log(`${id} 字幕匹配失败：${result.error || result.message}`, 'error'); } catch (error) { log(error.message, 'error'); alert(error.message); } finally { const current = document.querySelector(`[data-upload="${id}"]`); if (current) { current.disabled = false; current.textContent = '上传并识别'; } } }
async function renderSchemesBatch() { if (!project) return alert('请先导入 Excel'); try { await saveSettings(false); const planned = $('capacitySummary').dataset.planned || ''; log(planned ? `计划生成：${planned} 条，已生成：0 / ${planned}` : '正在按产能组合批量渲染'); const result = await api(`/api/projects/${project.project_id}/render`, { method: 'POST', body: new FormData() }); $('outputCount').textContent = Number($('outputCount').textContent) + result.count; result.outputs.forEach(output => { $('outputList').innerHTML = `<div class="output-item">${escapeHtml(output)}</div>` + $('outputList').innerHTML; }); log(`批量渲染完成，共生成 ${result.count} 条`, 'success'); } catch (error) { log(`渲染失败：${error.message}`, 'error'); alert(error.message); } }
async function importXlsx() { const file = $('xlsx').files[0]; if (!file) return alert('请选择 Excel 文件'); const fd = new FormData(); fd.append('file', file); try { project = await api('/api/projects/import-xlsx', { method: 'POST', body: fd }); selectedSchemeId = project.schemes[0]?.scheme_id || null; $('outputLimit').value = project.output_limit || 0; $('capacitySummary').textContent = '预计产能：未预检'; $('capacitySummary').dataset.planned = ''; $('projectState').textContent = `已导入：${project.project_name} / ${project.sheet_name}`; renderMessages(); renderSchemes(); log(`已导入 ${project.project_name}`, project.errors?.length ? 'error' : 'success'); } catch (error) { $('errors').textContent = error.message; $('errors').style.display = 'block'; log(error.message, 'error'); } }
async function saveSettings(showLog = true) { if (!project) return project; const rawLimit = $('outputLimit').value.trim(); if (!/^\d*$/.test(rawLimit)) throw Error('生成数量必须是大于或等于 0 的整数'); const fd = new FormData(); fd.append('hook_dir', $('hookDir').value); fd.append('output_dir', $('outputDir').value); fd.append('output_limit', rawLimit || '0'); project = await api(`/api/projects/${project.project_id}/settings`, { method: 'POST', body: fd }); renderMessages(); renderBodyPreview(); $('outputLimit').value = project.output_limit || 0; if (showLog) log('目录和生成数量已保存', 'success'); return project; }
async function updateBodyConfig(schemeId, rowKey, field, value) { const fd = new FormData(); fd.append('scheme_id', schemeId); fd.append('row_key', rowKey); fd.append(field, value); fd.append('changed_by', field); try { const result = await api(`/api/projects/${project.project_id}/body-config`, { method: 'POST', body: fd }); project = result.project; renderMessages(); renderBodyPreview(); log(`${schemeId} / ${rowKey} 已更新${field === 'threshold' ? '阈值' : '候选数量'}`, 'success'); await preflight(); } catch (error) { log(`Body 配置更新失败：${error.message}`, 'error'); alert(error.message); renderBodyPreview(); } }
async function mapBodyFolder(rowKey, directory) { const fd = new FormData(); fd.append('group_key', rowKey); fd.append('directory', directory); project = await api(`/api/projects/${project.project_id}/mapping/body`, { method: 'POST', body: fd }); renderBodyPreview(); log(directory ? `${rowKey} 目录已设置` : `${rowKey} 目录已清除`, 'success'); await refreshSubtitlePreview(); }
async function chooseFolder(kind) { if (!window.videoMagicMix?.selectDirectory) { alert('当前是浏览器备用模式，请在 Electron 桌面版中使用文件夹选择器。'); return; } const value = await window.videoMagicMix.selectDirectory(kind); if (!value) return; const id = kind === 'hook' ? 'hookDir' : 'outputDir'; $(id).value = value; await saveSettings(); }
async function chooseBodyFolder(rowKey) { if (!window.videoMagicMix?.selectDirectory) { alert('当前是浏览器备用模式，请在 Electron 桌面版中使用文件夹选择器。'); return; } const value = await window.videoMagicMix.selectDirectory('body', rowKey); if (!value) return; try { await mapBodyFolder(rowKey, value); } catch (error) { log(error.message, 'error'); alert(error.message); } }
function clearFolder(kind) { const id = kind === 'hook' ? 'hookDir' : 'outputDir'; $(id).value = ''; saveSettings(); }
async function clearBodyFolder(rowKey) { const button = document.querySelector(`[data-body-clear="${rowKey}"]`); const row = button?.closest('.body-preview-row'); const pathInput = row?.querySelector('.body-folder-path'); if (pathInput) pathInput.value = ''; try { await mapBodyFolder(rowKey, ''); } catch (error) { log(error.message, 'error'); alert(error.message); } }
$('import').onclick = importXlsx; $('preflight').onclick = preflight; $('renderSelected').onclick = renderSchemesBatch; $('schemeSelect').onchange = () => { selectedCueIndex = 0; renderBodyPreview(); renderSubtitleStyle(); refreshSubtitlePreview(); }; $('subtitlePreviewSegment').onchange = () => { selectedCueIndex = 0; renderSubtitleCues(); refreshSubtitlePreview(); }; $('refreshSubtitlePreview').onclick = refreshSubtitlePreview; ['subtitleFont','subtitleFontSize','subtitleBold','subtitlePrimaryColor','subtitleOutlineColor','subtitleOutlineWidth','subtitleShadow','subtitleAlignment','subtitleMargin','subtitleMaxWidth','subtitleMaxChars'].forEach(id => { $(id).addEventListener('change', scheduleSubtitleStyleSave); $(id).addEventListener('input', scheduleSubtitleStyleSave); }); $('clearLog').onclick = () => { $('statusLog').innerHTML = ''; $('logCount').textContent = '0'; }; document.querySelectorAll('[data-folder]').forEach(button => button.onclick = () => chooseFolder(button.dataset.folder)); document.querySelectorAll('[data-clear]').forEach(button => button.onclick = () => clearFolder(button.dataset.clear)); api('/api/health').then(data => { $('health').textContent = `${data.name} 已连接`; }).catch(() => { $('health').textContent = '后端未连接'; $('health').className = 'bad'; }); loadFonts();

// Keep the detailed preflight report in the right-hand log as well as the diagnostic panel.
async function preflight() {
  if (!project) return alert('请先导入 Excel');
  try {
    await saveSettings(false);
    const result = await api(`/api/projects/${project.project_id}/preflight`, { method: 'POST', body: new URLSearchParams() });
    if (result.body_configs) { project.body_configs = result.body_configs; renderBodyPreview(); }
    $('preflightResult').classList.remove('hidden');
    $('preflightResult').textContent = JSON.stringify(result, null, 2);
    const planned = result.planned_outputs ?? Math.min(result.estimated_outputs || 0, result.output_limit || result.estimated_outputs || 0);
    $('taskCount').textContent = result.ok ? `${planned} 条` : `${result.failures.length} 个问题`;
    $('capacitySummary').textContent = `预计产能：${result.estimated_outputs || 0} 条，本次计划：${planned} 条`;
    $('hookStatus').textContent = `已扫描 ${result.hook_count || 0} 个 Hook，全部作为完整候选`;
    $('capacitySummary').dataset.planned = planned;
    const estimates = result.scheme_estimates || [];
    for (const item of (result.plan || []).filter(item => item.row_key?.toLowerCase() !== 'hook').reverse()) {
      log(`${item.display_name || item.scheme_id} / ${item.row_key}：阈值 ${item.threshold ?? '--'}，候选 ${item.actual_candidate_count ?? item.candidate_count ?? 0} 个，素材 ${item.scanned_video_count ?? 0} 个，目标 ${Number(item.target_duration ?? item.target ?? 0).toFixed(2)} 秒`, item.actual_candidate_count ? 'success' : 'error');
    }
    for (const estimate of [...estimates].reverse()) {
      log(`${estimate.display_name}：预计 ${estimate.estimated_outputs} 条，本次计划 ${estimate.planned_outputs ?? estimate.estimated_outputs} 条，候选数量 ${estimate.candidate_counts.join(' × ')}`, estimate.estimated_outputs ? 'success' : 'error');
    }
    if (result.ok) {
      log(`预检通过，预计可生成 ${result.estimated_outputs} 条，本次计划生成 ${planned} 条`, 'success');
      $('errors').textContent = '';
      $('errors').style.display = 'none';
    } else {
      log(`预检发现 ${result.failures.length} 个问题，当前预计 ${result.estimated_outputs} 条`, 'error');
      for (const failure of [...result.failures].reverse()) log(`预检失败：${failure}`, 'error');
      $('errors').textContent = result.failures.join('\\n');
      $('errors').style.display = 'block';
    }
  } catch (error) {
    log(`预检请求失败：${error.message}`, 'error');
  }
}
