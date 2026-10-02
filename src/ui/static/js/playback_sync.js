/** Correct small media drift without repeated seeks interrupting the decoder. */
export function driftCorrection(masterTime, followerTime, speed) {
    const drift = masterTime - followerTime;
    if (Math.abs(drift) > .15) return {seek: masterTime, rate: speed};
    const correction = Math.abs(drift) > .008
        ? Math.max(-speed * .08, Math.min(speed * .08, drift * 2.5))
        : 0;
    return {seek: null, rate: speed + correction};
}
