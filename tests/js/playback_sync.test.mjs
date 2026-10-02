import assert from 'node:assert/strict';
import test from 'node:test';
import {driftCorrection} from '../../src/ui/static/js/playback_sync.js?v=20261002v1';

test('small drift converges at each supported practice speed without seeking', () => {
    for (const speed of [.75, 1, 1.5]) {
        for (const offset of [-.06, .06]) {
            let master = 10, follower = master + offset;
            for (let step = 0; step < 80; step++) {
                const result = driftCorrection(master, follower, speed);
                assert.equal(result.seek, null);
                assert.ok(Math.abs(result.rate - speed) <= speed * .08 + 1e-9);
                master += speed * .025;
                follower += result.rate * .025;
            }
            assert.ok(Math.abs(master - follower) < .009);
        }
    }
});

test('large discontinuities seek and aligned tracks keep the chosen rate', () => {
    assert.deepEqual(driftCorrection(10, 9, 1.5), {seek: 10, rate: 1.5});
    assert.deepEqual(driftCorrection(10, 10.002, .75), {seek: null, rate: .75});
});
