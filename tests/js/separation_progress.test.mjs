import assert from 'node:assert/strict';
import test from 'node:test';
import {formatSeparationProgress, createSeparationController} from '../../src/ui/static/js/separation_controller.js?v=20261002v1';
import {state} from '../../src/ui/static/js/app_state.js?v=20261002v1';

test('model preparation has a stage without an invented percentage', () => {
    const view = formatSeparationProgress({stage:'loading_model', progress:null, device:'cpu'});
    assert.equal(view.text, '加载模型 · CPU');
    assert.equal(view.fraction, null);
});

test('inference uses completed chunks rather than a stale overall fraction', () => {
    const view = formatSeparationProgress({stage:'separating_audio', progress:.95, completed:2, total:23, unit:'chunks'});
    assert.equal(view.text, '分离音轨 · 2/23 片段（9%）');
    assert.equal(view.fraction, 2/23);
});

test('each measured stage has its own counter', () => {
    const inference = formatSeparationProgress({stage:'separating_audio', completed:23, total:23, unit:'chunks'});
    const saving = formatSeparationProgress({stage:'saving_results', completed:1, total:6, unit:'stems'});
    assert.equal(inference.fraction, 1);
    assert.equal(saving.text, '保存结果 · 1/6 音轨');
    assert.equal(saving.fraction, 1/6);
    assert.doesNotMatch(inference.text, /分离完成/);
});

test('unknown download length shows bytes without a completion percentage', () => {
    const view = formatSeparationProgress({stage:'downloading_model', completed:5*1048576, total:null, unit:'bytes'});
    assert.equal(view.text, '下载模型文件 · 已下载 5.0 MB');
    assert.equal(view.fraction, null);
});

test('terminal failures never show a previous completed stage as 100 percent', () => {
    const view = formatSeparationProgress({stage:'failed', completed:6, total:6, unit:'stems', error:'保存失败'});
    assert.equal(view.text, '分离失败：保存失败');
    assert.equal(view.fraction, null);
});

test('controller renders stages and ignores older tasks and snapshots', () => {
    const nodes = new Map();
    const devices = [{value:'cpu',checked:true}, {value:'gpu',checked:false}];
    globalThis.document = {querySelectorAll: () => devices, getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, {textContent:'', setAttribute(){}, closest(){return null;}, classList:{toggle(){},remove(){},add(){}}});
        return nodes.get(id);
    }};
    state.separating = true;
    state.separationTaskId = 'current';
    state.separationProgress = null;
    const controller = createSeparationController({});
    controller.updateSepProgress({task_id:'current', updated_at:20, stage:'separating_audio', completed:2, total:4, unit:'chunks', device:'gpu'});
    assert.equal(nodes.get('sep-status').textContent, '分离音轨 · 2/4 片段（50%） · GPU');
    assert.equal(nodes.get('sep-ring-label-2').textContent, '阶段 50%');
    assert.equal(devices[0].checked, false);
    assert.equal(devices[1].checked, true);
    assert.equal(devices[1].disabled, true);
    controller.updateSepProgress({task_id:'current', updated_at:10, stage:'loading_model'});
    controller.updateSepProgress({task_id:'previous', updated_at:30, stage:'done'});
    assert.equal(state.separationProgress.updated_at, 20);
    assert.equal(nodes.get('sep-status').textContent, '分离音轨 · 2/4 片段（50%） · GPU');
    state.separating = false;
    controller.updateSepProgress({task_id:'current', updated_at:25, stage:'done', device:'gpu'});
    assert.equal(devices[1].disabled, false);
    assert.equal(nodes.get('sep-status').textContent, '分离完成 · GPU');
    devices[0].checked = true;
    devices[1].checked = false;
    controller.renderSepProgress();
    assert.equal(devices[0].checked, true);
    assert.equal(devices[1].checked, false);
});
