import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

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
        makeCard('piano', 'chord_chordnet_2e1d'),
        makeCard('guitar', 'chord_chordnet_2e1d'),
    ];
    const source = fs.readFileSync(
        new URL('../../src/ui/static/js/app.js', import.meta.url),
        'utf8',
    )
        .replace(/^import .*;$/gm, '')
        .concat(`
            globalThis.__tab3Test = {
                state,
                handleRunAllAnalyses,
                onAnalysisDone,
            };
        `);

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
    const context = {
        console,
        document,
        window: {},
        confirm: () => true,
        requestAnimationFrame() {},
        setTimeout,
        clearTimeout,
        api: {
            async analyze(wid, track, plugin) {
                calls.push({ wid, track, plugin });
                return { ok: true };
            },
        },
        EventStream: class {
            on() { return this; }
            connect() {}
            setWorkshopId() {}
        },
        drawPlayhead() {},
        setTimeout(callback, delay) {
            if (delay === 0) return globalThis.setTimeout(callback, delay);
            return 0;
        },
        clearTimeout() {},
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    context.__tab3Test.state.currentWid = 'workshop-test';
    context.__tab3Test.state.selectedTracks = new Set(['piano', 'guitar']);
    context.__tab3Test.state.analyzerSelections = {
        piano: 'chord_chordnet_2e1d',
        guitar: 'chord_chordnet_2e1d',
    };
    return { calls, app: context.__tab3Test };
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
        plugin: 'chord_chordnet_2e1d',
        result: { chords: [] },
    });
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(calls.length, 2);
});
