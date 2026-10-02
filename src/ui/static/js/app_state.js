export const TRACKS = ['vocals', 'drums', 'bass', 'piano', 'guitar', 'other'];
export const TRACK_LABELS = {
    full: '原曲', vocals: '人声', drums: '鼓', bass: '贝斯',
    piano: '钢琴', guitar: '吉他', other: '其他',
};
export const TRACK_COLORS = {
    vocals: '#5b65ff', drums: '#ff9500', bass: '#34c759',
    piano: '#af52de', guitar: '#ff2d55', other: '#8e8e93',
};

export const state = {
    workshops: [],          // list {id, name, last_tab, active}
    currentWid: null,       // 当前 active 车间
    step: 1,
    hasRawAudio: false,
    separated: false,
    separating: false,
    separationTaskId: null,
    separationProgress: null,
    availableTracks: [],
    selectedTracks: new Set(),
    selectionSaving: false,
    analysisResults: {},
    analysisResultPlugins: {},
    analyzerSelections: {},
    analysisPendingPlugins: {},
    analysisRunning: new Set(),
    analysisTaskIds: {},
    analysisBatchQueue: [],
    analysisBatchCurrent: null,
    analysisBatchRunning: false,
    trackVizData: {},
    audioElements: new Map(),
    tab4LoadToken: 0,
    timelineZoom: 1,
    busy: false,            // 任何"切/关/删/新建"进行中
    // playback
    playing: false,
    currentTime: 0,
    duration: 30,
    speed: 1,
    raf: null,
    lastTs: 0,
    mix: {},
    source: 'full',
    loop: {a:null, b:null, enabled:false},
    follow: true,
    analysisErrors: {},
    resultSelections: {},
};
