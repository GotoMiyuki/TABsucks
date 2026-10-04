import assert from 'node:assert/strict';
import test from 'node:test';
import api from '../../src/ui/static/js/api.js?v=20261002v1';
import { state } from '../../src/ui/static/js/app_state.js?v=20261002v1';
import { createAnalysisController } from '../../src/ui/static/js/analysis_controller.js?v=20261002v1';

const initialState = structuredClone(state);

async function render(plugins) {
    const container = { innerHTML: '', querySelectorAll() { return []; } };
    const runAll = { disabled: false, textContent: '' };
    globalThis.document = {
        getElementById(id) {
            if (id === 'analysis-config-list') return container;
            if (id === 'btn-run-all') return runAll;
            return null;
        },
        querySelectorAll() { return []; },
    };
    Object.assign(state, structuredClone(initialState));
    state.selectedTracks = new Set(['piano']);
    state.analyzerSelections = { piano: 'chord_btc_sl' };
    api.listAnalyzerPlugins = async () => ({ ok: true, data: plugins });
    const controller = createAnalysisController({ showToast() {}, updateNavigationControls() {} });
    await controller.loadAnalyzerPlugins();
    controller.initializeAnalyzerSelections();
    controller.renderAnalysisConfig();
    return { html: container.innerHTML, runAll };
}

test('missing BTC assets stay visible but disabled and select the runnable alternative', async () => {
    const { html, runAll } = await render([
        { name: 'chord_btc_sl', display_name: 'BTC-SL', input_stems: ['piano'],
            assets_ready: false, unavailable_reason: 'Missing ChordMini' },
        { name: 'chord_ismir2019', input_stems: ['piano'] },
    ]);
    assert.equal(state.analyzerSelections.piano, 'chord_ismir2019');
    assert.match(html, /value="chord_btc_sl"[^>]*disabled/);
    assert.match(html, /Missing ChordMini/);
    assert.equal(runAll.disabled, false);
});

test('a missing model cannot be queued when it is the only matching analyzer', async () => {
    const { html, runAll } = await render([
        { name: 'chord_btc_sl', input_stems: ['piano'], assets_ready: false,
            unavailable_reason: 'Missing weights' },
    ]);
    assert.equal(state.analyzerSelections.piano, undefined);
    assert.equal(runAll.disabled, true);
    assert.match(html, /模型资源未就绪/);
});

test('installed BTC resources enable normal model selection', async () => {
    const { html, runAll } = await render([
        { name: 'chord_btc_sl', input_stems: ['piano'], assets_ready: true },
    ]);
    assert.equal(state.analyzerSelections.piano, 'chord_btc_sl');
    assert.doesNotMatch(html, /value="chord_btc_sl"[^>]*disabled/);
    assert.equal(runAll.disabled, false);
});
