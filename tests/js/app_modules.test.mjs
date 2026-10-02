import assert from 'node:assert/strict';
import test from 'node:test';

test('real frontend module graph initializes with shared state', async () => {
    let initialize;
    const node = () => ({
        addEventListener() {}, querySelectorAll() { return []; },
        setAttribute() {}, replaceChildren() {}, closest() { return null; },
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        style: { setProperty() {} }, dataset: {},
        innerHTML: '', textContent: '', disabled: false,
    });
    const nodes = new Map();
    globalThis.document = {
        addEventListener(name, callback) { if (name === 'DOMContentLoaded') initialize = callback; },
        querySelectorAll() { return []; },
        getElementById(id) {
            if (!nodes.has(id)) nodes.set(id, node());
            return nodes.get(id);
        },
        body: node(),
    };
    globalThis.window = { addEventListener() {}, matchMedia() { return { addEventListener() {} }; } };
    globalThis.ResizeObserver = class { observe() {} };
    const api = (await import('../../src/ui/static/js/api.js?v=20261002v1')).default;
    api.listWorkshops = async () => ({ ok: true, data: [] });
    api.listSeparatorPlugins = async () => ({ ok: true, data: [] });
    api.createEventStream = () => ({ close() {} });
    await import('../../src/ui/static/js/app.js?v=20261002v1');
    assert.equal(typeof initialize, 'function');
    await initialize();
    const { state } = await import('../../src/ui/static/js/app_state.js?v=20261002v1');
    assert.deepEqual(state.workshops, []);
    assert.equal(state.currentWid, null);
});
