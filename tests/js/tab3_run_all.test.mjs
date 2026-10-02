import assert from 'node:assert/strict';
import test from 'node:test';
import api from '../../src/ui/static/js/api.js?v=20261002v1';
import { state } from '../../src/ui/static/js/app_state.js?v=20261002v1';
import { createAnalysisController } from '../../src/ui/static/js/analysis_controller.js?v=20261002v1';
const initialState = structuredClone(state);

function makeCard(track, plugin) {
    const select = { value: plugin, disabled: false, dataset: { track } };
    const button = { textContent: '', disabled: false };
    const status = { textContent: '', className: '' };
    return {
        dataset: { track },
        querySelector(selector) {
            if (selector === '.sel-analyzer') return select;
            if (selector === '.btn-run-analysis') return button;
            if (selector === '.analysis-status') return status;
            return null;
        },
    };
}

function loadAppHarness() {
    const calls = [];
    const cards = [
        makeCard('piano', 'chord_ismir2019'),
        makeCard('guitar', 'chord_ismir2019'),
    ];
    const document = {
        body: { appendChild() {} },
        addEventListener() {},
        createElement() {
            return {
                id: '',
                className: '',
                textContent: '',
                style: {},
            };
        },
        getElementById() { return null; },
        querySelectorAll(selector) {
            return selector === '.analysis-track-card' ? cards : [];
        },
        querySelector(selector) {
            const match = selector.match(
                /^\.sel-analyzer\[data-track="([^"]+)"\]$/,
            );
            if (match) {
                return cards.find(card => card.dataset.track === match[1])
                    ?.querySelector('.sel-analyzer') || null;
            }
            const cardMatch = selector.match(
                /^\.analysis-track-card\[data-track="([^"]+)"\]$/,
            );
            if (cardMatch) {
                return cards.find(card => card.dataset.track === cardMatch[1])
                    || null;
            }
            return null;
        },
    };
    globalThis.document = document;
    Object.assign(state, structuredClone(initialState));
    api.analyze = async (wid, track, plugin) => {
        calls.push({ wid, track, plugin });
        return { ok: true };
    };
    const app = { state, ...createAnalysisController({
        showToast() {}, updateNavigationControls() {},
    }) };
    state.currentWid = 'workshop-test';
    state.selectedTracks = new Set(['piano', 'guitar']);
    state.analyzerSelections = {
        piano: 'chord_ismir2019', guitar: 'chord_ismir2019',
    };
    return { calls, app };
}

test('run all waits for one track to finish before launching the next', async () => {
    const { calls, app } = loadAppHarness();

    await app.handleRunAllAnalyses();

    assert.equal(
        calls.length,
        1,
        'only the first analysis may start before its terminal SSE event',
    );
    app.onAnalysisDone({
        track: 'piano',
        plugin: 'chord_ismir2019',
        result: { chords: [] },
    });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(calls.length, 2);
});
