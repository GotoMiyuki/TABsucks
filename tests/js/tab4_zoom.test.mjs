import assert from 'node:assert/strict';
import test from 'node:test';

import {
    calculateTimelineLayout,
    clampTimelineZoom,
} from '../../src/ui/static/js/timeline_zoom.js';

test('timeline zoom keeps the current playback time centered', () => {
    const layout = calculateTimelineLayout({
        viewportWidth: 1000,
        labelWidth: 100,
        zoom: 4,
        currentTime: 50,
        duration: 100,
    });

    assert.equal(layout.contentWidth, 3600);
    assert.equal(layout.scrollLeft, 1350);
});

test('timeline zoom clamps the beginning, end, and zoom range', () => {
    assert.equal(clampTimelineZoom(0), 1);
    assert.equal(clampTimelineZoom(99), 16);

    const beginning = calculateTimelineLayout({
        viewportWidth: 1000,
        labelWidth: 100,
        zoom: 4,
        currentTime: 0,
        duration: 100,
    });
    const end = calculateTimelineLayout({
        viewportWidth: 1000,
        labelWidth: 100,
        zoom: 4,
        currentTime: 100,
        duration: 100,
    });

    assert.equal(beginning.scrollLeft, 0);
    assert.equal(end.scrollLeft, 2700);
});
