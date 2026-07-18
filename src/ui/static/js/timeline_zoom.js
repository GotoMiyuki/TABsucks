const MIN_TIMELINE_ZOOM = 1;
const MAX_TIMELINE_ZOOM = 16;

export function clampTimelineZoom(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return MIN_TIMELINE_ZOOM;
    return Math.max(MIN_TIMELINE_ZOOM, Math.min(MAX_TIMELINE_ZOOM, numeric));
}

export function calculateTimelineLayout({
    viewportWidth,
    labelWidth,
    zoom,
    currentTime,
    duration,
}) {
    const safeViewport = Math.max(1, Number(viewportWidth) || 0);
    const safeLabel = Math.max(0, Number(labelWidth) || 0);
    const visibleTimelineWidth = Math.max(1, safeViewport - safeLabel);
    const contentWidth = visibleTimelineWidth * clampTimelineZoom(zoom);
    const proportion = Number(duration) > 0
        ? Math.max(0, Math.min(1, (Number(currentTime) || 0) / duration))
        : 0;
    const maxScrollLeft = Math.max(0, contentWidth - visibleTimelineWidth);
    const centered = proportion * contentWidth - visibleTimelineWidth / 2;

    return {
        contentWidth,
        scrollLeft: Math.max(0, Math.min(maxScrollLeft, centered)),
    };
}
