
# TABsucks UML 模型文件

---

## 1. 用例图

### 1.1 系统用例图

```mermaid

flowchart TD
    subgraph TABsucks_System
        subgraph Tab1_AudioInput["Tab1: 音频输入"]
            UC1([导入本地音频])
            UC2([导入YouTube音频])
            UC3([导入Bilibili音频])
        end

        subgraph Tab2_Separation["Tab2: 音轨分离"]
            UC4([选择分离模型])
            UC5([执行音轨分离])
            UC6([切换分离模型])
        end

        subgraph Tab3_Analysis["Tab3: 分析处理"]
            UC7([选择音轨])
            UC8([执行节奏分析])
            UC9([执行和弦分析])
        end

        subgraph Tab4_Playback["Tab4: 播放/导出"]
            UC10([播放控制])
            UC11([调速播放])
            UC12([循环播放])
            UC13([轨道静音/独奏])
            UC14([导出MIDI])
            UC15([可视化查看])
        end

        subgraph Global["全局功能"]
            UC16([创建车间])
            UC17([加载车间])
            UC18([保存车间])
            UC19([切换车间])
        end
    end

    User((用户))

    User --> UC1
    User --> UC2
    User --> UC3
    User --> UC4
    User --> UC5
    User --> UC7
    User --> UC8
    User --> UC9
    User --> UC10
    User --> UC11
    User --> UC12
    User --> UC13
    User --> UC14
    User --> UC15
    User --> UC16
    User --> UC17
    User --> UC18
    User --> UC19

    UC5 -->|.生成6轨| UC7
    UC6 -->|.重新分离| UC5

    UC1 -.->|提供音频| UC4
    UC2 -.->|提供音频| UC4
    UC3 -.->|提供音频| UC4
```

### 1.2 用例说明

| 用例 | 描述 | 参与者 |
|-------|------|--------|
| UC1~UC3 | 音频输入（本地/YTB/B站） | Tab1 |
| UC4~UC6 | 音轨分离（选择/执行/切换） | Tab2 |
| UC7~UC9 | 分析处理（选择音轨/节奏/和弦） | Tab3 |
| UC10~UC15 | 播放/导出（播放/调速/循环/静音/MIDI/可视化） | Tab4 |
| UC16~UC19 | 车间管理（创建/加载/保存/切换） | 全局 |

---

## 2. 类图

### 2.1 核心类图

```mermaid
classDiagram
    %% 界面层
    class TabView {
        <<abstract>>
        +workshop: MusicWorkshop
        +load()
        +refresh()
        +isStale(): bool
    }

    class AudioInputTab {
        +loadFromFile(path: string)
        +loadFromURL(url: string)
        +displayWaveform()
    }

    class SeparationTab {
        +selectModel(model: string)
        +runSeparation()
        +getProgress(): float
    }

    class AnalysisTab {
        +selectStem(stem: Stem)
        +runRhythmAnalysis()
        +runChordAnalysis()
    }

    class PlaybackTab {
        +play()
        +pause()
        +setSpeed(rate: float)
        +setLoop(start: float, end: float)
        +muteStem(name: string)
        +soloStem(name: string)
        +exportMIDI()
    }

    %% 数据层
    class MusicWorkshop {
        -id: string
        -name: string
        -audio: AudioFile
        -stems: dict~string, Stem~
        -analysisResults: dict~string, AnalysisResult~
        -status: WorkshopStatus
        +create()
        +save()
        +load()
        +switch()
    }

    class MusicWorkshopManager {
        -workshops: list~MusicWorkshop~
        -currentIndex: int
        +createWorkshop(name: string)
        +switchTo(index: int)
        +deleteWorkshop(index: int)
        +listWorkshops()
    }

    class AudioFile {
        -path: string
        -url: string
        -sampleRate: int
        -duration: float
        -waveform: np.array
        +load()
        +loadFromURL(url)
    }

    class Stem {
        -name: string
        -audio: np.array
        -isProcessed: bool
    }

    class AnalysisResult {
        -type: string
        -data: dict
        -timestamp: datetime
    }

    class RhythmResult {
        -bpm: float
        -timeSignature: string
        -beatPositions: list
        -pattern: string
    }

    class ChordResult {
        -chords: list~ChordAnnotation~
        -timePositions: list
    }

    class ChordAnnotation {
        -root: string
        -quality: string
        -bass: string
        -startTime: float
        -endTime: float
    }

    %% 模型层
    class SeparationModel {
        -name: string
        -isLocal: bool
        -apiKey: string
        +separate(audio: AudioFile): dict~string, Stem~
    }

    class RhythmModel {
        -name: string
        +analyze(stem: Stem): RhythmResult
    }

    class ChordModel {
        -name: string
        +analyze(stem: Stem): ChordResult
    }

    %% 播放器
    class Player {
        -currentTime: float
        -speed: float
        -isPlaying: bool
        -loopStart: float
        -loopEnd: float
        -mutedStems: set
        +play()
        +pause()
        +seek(time: float)
    }

    %% 关系
    TabView <|-- AudioInputTab
    TabView <|-- SeparationTab
    TabView <|-- AnalysisTab
    TabView <|-- PlaybackTab

    AudioInputTab --> AudioFile
    SeparationTab --> SeparationModel
    AnalysisTab --> RhythmModel
    AnalysisTab --> ChordModel
    PlaybackTab --> Player

    MusicWorkshopManager --> "*" MusicWorkshop
    MusicWorkshop --> AudioFile
    MusicWorkshop --> "*" Stem
    MusicWorkshop --> "*" AnalysisResult

    RhythmResult --|> AnalysisResult
    ChordResult --|> AnalysisResult

    SeparationModel --> Stem
    RhythmModel --> RhythmResult
    ChordModel --> ChordResult
```

### 2.2 类说明

| 类名 | 属性 | 方法 | 说明 |
|------|------|------|------|
| TabView | workshop | load(), refresh() | 抽象Tab基类 |
| AudioInputTab | - | loadFromFile(), loadFromURL() | Tab1音频输入 |
| SeparationTab | progress | selectModel(), runSeparation() | Tab2音轨分离 |
| AnalysisTab | - | selectStem(), runRhythm/ChordAnalysis() | Tab3分析处理 |
| PlaybackTab | - | play(), setSpeed(), exportMIDI() | Tab4播放导出 |
| MusicWorkshop | name, audio, stems | create(), save(), switch() | 车间状态容器 |
| MusicWorkshopManager | workshops | createWorkshop(), switchTo() | 车间管理器 |
| AudioFile | path, waveform | load(), loadFromURL() | 音频文件 |
| Stem | name, audio | - | 分离后的单轨 |
| SeparationModel | name, isLocal | separate() | 分离模型抽象 |
| RhythmModel | - | analyze() | 节奏分析模型 |
| ChordModel | - | analyze() | 和弦分析模型 |
| Player | currentTime, speed | play(), pause(), seek() | 音频播放器 |

---

## 3. 活动图

### 3.1 主流程活动图

```mermaid
flowchart TD
    Start((开始)) --> CreateWS[创建/选择车间]
    CreateWS --> ChooseTab[选择Tab]

    %% Tab1路径
    ChooseTab -->|Tab 1| InputSrc{选择输入源}
    InputSrc -->|本地| LoadLocal[加载本地音频]
    InputSrc -->|YouTube| LoadYT[获取YouTube音频]
    InputSrc -->|Bilibili| LoadBili[获取Bili音频]
    LoadLocal --> Disp1[显示波形]
    LoadYT --> Disp1
    LoadBili --> Disp1
    Disp1 --> CheckSep[是否有分离结果?]

    %% Tab2路径
    ChooseTab -->|Tab 2| SepModel{选择模型}
    SepModel -->|本地模型| SelectLocal[选择本地模型]
    SepModel -->|API| InputAPI[输入API Key]
    SelectLocal --> RunSep[执行分离]
    InputAPI --> RunSep
    RunSep --> Progress{显示进度}
    Progress -->|完成| SaveStems[保存6轨]
    SaveStems --> CheckSep
    CheckSep -->|有| GotoTab3[进入Tab3]
    CheckSep -->|无| ChooseTab

    %% Tab3路径
    GotoTab3 -->|Tab 3| SelectStem[选择音轨]
    SelectStem --> ChooseAn{选择分析}
    ChooseAn -->|节奏分析| RunRhythm[执行节奏分析]
    ChooseAn -->|和弦分析| RunChord[执行和弦分析]
    RunRhythm --> SaveRhythm[保存结果]
    RunChord --> SaveChord[保存结果]
    SaveRhythm --> GotoTab4[进入Tab4]
    SaveChord --> GotoTab4

    %% Tab4路径
    ChooseTab -->|Tab 4| PlayCtl{播放控制}
    PlayCtl -->|播放| Play[播放音频]
    PlayCtl -->|调速| SetSpeed[设置0.5x~2x]
    PlayCtl -->|循环| SetLoop[设置A-B循环]
    PlayCtl -->|静音| MuteStem[静音轨道]
    PlayCtl -->|导出| ExportMIDI[导出MIDI]
    Play --> ViewWave[显示波形+标记]
    SetSpeed --> ViewWave
    SetLoop --> ViewWave
    MuteStem --> ViewWave

    ViewWave --> Continue{继续?}
    Continue -->|是| ChooseTab
    Continue -->|否| End((结束))
```

### 3.2 状态过期刷新流程

```mermaid
flowchart LR
    subgraph 上游更新流程
    ChangeModel[Tab2更换分离模型] --> ReRunSep[重新执行分离]
    ReRunSep --> UpdateStems[更新音轨]
    UpdateStems --> MarkStale[标记下游状态为过期]
    end

    subgraph 下游响应流程
    MarkStale --> ShowPrompt[显示提示:分析结果已过期]
    ShowPrompt --> UserChoice{用户选择}
    UserChoice -->|刷新| ReAnalyze[重新分析]
    UserChoice -->|忽略| Continue[继续使用旧结果]
    ReAnalyze --> UpdateResult[更新分析结果]
    end
```

### 3.3 车间切换流程

```mermaid
flowchart TD
    Start((开始)) --> ListWS[列出所有车间]
    ListWS --> SelectWS{选择车间}
    SelectWS -->|新建| NewName[输入车间名]
    NewName --> CreateWS[创建车间]
    SelectWS -->|加载| LoadWS[加载车间状态]
    CreateWS --> InitTab[初始化四个Tab]
    LoadWS --> RestoreTab[恢复四Tab状态]
    InitTab --> Ready[就绪]
    RestoreTab --> Ready
    Ready --> End((结束))
```

---

## 4. 序列图（关键交互）

### 4.1 音轨分离序列图

```mermaid
sequenceDiagram
    participant User
    participant AudioInputTab
    participant SeparationTab
    participant MusicWorkshop
    participant SeparationModel

    User->>AudioInputTab: 导入音频
    AudioInputTab->>AudioFile: load()
    AudioFile-->>AudioInputTab: 波形数据
    AudioInputTab->>MusicWorkshop: 保存音频
    User->>SeparationTab: 选择分离模型
    SeparationTab->>SeparationModel: selectModel()
    User->>SeparationTab: 执行分离
    SeparationTab->>SeparationModel: separate(audio)
    SeparationModel-->>SeparationTab: 6个Stem
    SeparationTab->>MusicWorkshop: 保存stems
    MusicWorkshop-->>User: 分离完成
```

### 4.2 分析处理序列图

```mermaid
sequenceDiagram
    participant User
    participant AnalysisTab
    participant MusicWorkshop
    participant RhythmModel
    participant ChordModel

    User->>AnalysisTab: 选择音轨(钢琴)
    AnalysisTab->>MusicWorkshop: getStem("钢琴")
    MusicWorkshop-->>AnalysisTab: Stem audio
    User->>AnalysisTab: 执行节奏分析
    AnalysisTab->>RhythmModel: analyze(stem)
    RhythmModel-->>AnalysisTab: RhythmResult
    AnalysisTab->>MusicWorkshop: 保存结果
    User->>AnalysisTab: 执行和弦分析
    AnalysisTab->>ChordModel: analyze(stem)
    ChordModel-->>AnalysisTab: ChordResult
    AnalysisTab->>MusicWorkshop: 保存结果
```

---

## 5. 状态图

### 5.1 车间状态图

```mermaid
stateDiagram-v2
    [*] --> Empty: 新建车间
    Empty --> Tab1_Loaded: 导入音频
    Tab1_Loaded --> Tab2_Separated: 执行分离
    Tab2_Separated --> Tab3_Analyzed: 执行分析
    Tab3_Analyzed --> Tab4_Ready: 进入Tab4

    Tab2_Separated --> Tab1_Loaded: 返回更换模型
    Tab3_Analyzed --> Tab2_Separated: 返回重新分离
    Tab4_Ready --> Tab2_Separated: 返回更换模型

    Tab1_Loaded --> Empty: 删除车间
    Tab2_Separated --> Empty: 删除车间
    Tab3_Analyzed --> Empty: 删除车间
    Tab4_Ready --> Empty: 删除车间

    Tab4_Ready --> Saved: 保存车间
    Saved --> [*]
```

---

## 6. 组件图

### 6.1 系统组件图

```mermaid
flowchart TB
    subgraph UI_Layer["UI层"]
        direction LR
        MainWindow
        TabWidget
        WaveformView
        PlayerControl
    end

    subgraph Business_Layer["业务层"]
        direction LR
        WorkshopManager
        SeparationService
        AnalysisService
        PlayerService
    end

    subgraph Model_Layer["模型层"]
        direction LR
        BSRoFormer
        Demucs
        BeatTransformer
        ChordRecognition
    end

    subgraph Data_Layer["数据层"]
        direction LR
        AudioFile
        Stem
        AnalysisResult
    end

    UI_Layer --> Business_Layer
    Business_Layer --> Model_Layer
    Business_Layer --> Data_Layer
    Model_Layer --> Data_Layer
```

---

## 7. 包图

### 7.1 系统包图

```mermaid
flowchart TB
    subgraph TABsucks
        direction TB
        subgraph UI["ui包"]
            Tab1_AudioInput
            Tab2_Separation
            Tab3_Analysis
            Tab4_Playback
        end

        subgraph Core["core包"]
            WorkshopManager
            Pipeline
            ModelRegistry
        end

        subgraph Service["service包"]
            AudioService
            SeparationService
            AnalysisService
            ExportService
        end

        subgraph Model["model包"]
            BSRoFormer
            Demucs
            BeatTransformer
            ChordModel
        end

        subgraph Data["data包"]
            AudioFile
            Stem
            AnalysisResult
        end
    end
```