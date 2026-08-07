import type { Catalog } from '../types'

/**
 * English catalog — the default locale and the fallback for every other locale.
 *
 * Every key here is the canonical key used by `t()`. Other locales translate
 * the same keys; any key missing from a non-English locale falls back to this
 * file (and finally to the key string itself, so missing translations are
 * visible during development rather than silently blank).
 *
 * Interpolation uses `{name}` placeholders, filled by the `params` argument of
 * `t(key, params)`.
 */
export const en: Catalog = {
  // ---- Language ----
  'language.en': 'English',
  'language.zh': '中文',

  // ---- Menu ----
  'menu.title': 'BATTLE CITY',
  'menu.subtitle': 'Faithful to the classic. Designed for the future.',
  'menu.resume.label': 'RESUME',
  'menu.resume.stageFormat': 'STAGE {n}',
  'menu.resume.info': 'Continue from your last manual save',
  'menu.resume.infoDetailed': 'Continue from Stage {stage} · {name} · Score {score}',
  'menu.resume.hint': 'Enter ↵',
  'menu.difficulty': 'DIFFICULTY',
  'menu.theme': 'THEME',
  'menu.language': 'LANGUAGE',
  'menu.stage': 'STAGE',
  'menu.start.newGame': 'NEW GAME',
  'menu.start.startGame': 'START GAME',
  'menu.start.hint': 'Enter ↵',
  'menu.controls': 'CONTROLS',
  'menu.controls.hint': 'Enter ↵',
  'menu.nav.select': '↑ ↓ Select',
  'menu.nav.change': '← → Change',
  'menu.hiscore': 'High Score: {score}',

  // ---- HUD labels ----
  'hud.score': 'SCORE',
  'hud.hi': 'HI',
  'hud.star': 'STAR',
  'hud.stage': 'STAGE',
  'hud.lives': 'LIVES',
  'hud.enemy': 'ENEMY',
  'hud.god': 'GOD ALLY',
  'hud.spectate': 'SPECTATE',
  'hud.speed': 'SPEED',
  'hud.replayMode': 'REPLAY MODE',
  'hud.guard': 'Guardian',
  'hud.frenzy': 'Frenzy',
  'hud.sacrifice': 'Sacrifice',
  'hud.rewind': 'Time Box',

  // ---- Pause ----
  'pause.title': 'PAUSED',
  'pause.hint': 'Press P to resume',
  'hud.pauseHint': 'P Resume',

  // ---- Game over ----
  'gameover.title': 'GAME OVER',
  'gameover.hint': 'Press Alt+R or Enter to return to menu',

  // ---- Stage clear ----
  'stageclear.title': 'STAGE CLEAR',
  'stageclear.name': 'Stage {n}: {name} Complete',

  // ---- Victory ----
  'victory.title': 'VICTORY!',
  'victory.score': 'Final Score: {score}',
  'victory.hint': 'Press Alt+R or Enter to play again',

  // ---- Recovery (mission failed) ----
  'recovery.title': 'MISSION FAILED',
  'recovery.subtitle': 'Rewind time and try again',
  'recovery.option.continue.label': 'Continue',
  'recovery.option.continue.desc': 'Accept defeat — classic game over',
  'recovery.option.loadLatest.label': 'Load Latest Snapshot',
  'recovery.option.loadLatest.desc': 'Return to the most recent safe moment',
  'recovery.option.replay.label': 'Replay This Stage',
  'recovery.option.replay.desc': 'Load the stage-start snapshot',
  'recovery.option.restart.label': 'Restart Without Loading',
  'recovery.option.restart.desc': 'Fresh stage start — no snapshot',
  'recovery.option.choose.label': 'Choose a Snapshot…',
  'recovery.option.choose.desc': 'Open the Snapshot Browser',
  'recovery.controls': '↑ ↓ Select    Enter Confirm    Alt+R Menu',
  'recovery.countdown': 'READY',

  // ---- Controls / key bindings panel ----
  'controls.title': 'KEY BINDINGS',
  'controls.hint': 'Click a key, then press a new one',
  'controls.reset': 'Reset Defaults',
  'controls.back': 'Back',
  'controls.escHint': 'Press Esc to go back',
  'controls.pressKey': 'Press a key…',
  'controls.actions.up': 'Move Up',
  'controls.actions.down': 'Move Down',
  'controls.actions.left': 'Move Left',
  'controls.actions.right': 'Move Right',
  'controls.actions.fire': 'Fire',
  'controls.actions.pause': 'Pause',
  'controls.actions.guard': 'Guardian',
  'controls.actions.frenzy': 'Frenzy',
  'controls.actions.rewind': 'Time Box',

  // ---- Footer ----
  'footer.pause': 'Pause',
  'footer.reset': 'Reset',
  'footer.save': 'Save',
  'footer.speed': 'Speed',

  // ---- Difficulty display names (override config `name`) ----
  'difficulty.relax': 'Relax',
  'difficulty.classic': 'Classic',
  'difficulty.hard': 'Hard',
  'difficulty.chaos': 'Chaos',

  // ---- Theme display names (override config `name`) ----
  'theme.classic': 'Classic',
  'theme.neon': 'Neon',
  'theme.modern': 'Modern Retro',

  // ---- Toast notifications ----
  'toast.perfCopied': 'Performance report copied',
  'toast.gameSaved': 'Game saved',
  'toast.snapshotDeleted': 'Snapshot deleted',
  'toast.snapshotCapacity': 'Snapshot storage full — oldest auto-removed',
  'toast.rewindReady': 'Rewind ready',
  'toast.languageSet': 'Language set to {name}',
  'toast.controlsReset': 'Key bindings reset',
  'toast.keyConflict': 'That key is already used',
  'toast.replayCopied': 'Replay copied to clipboard',
  'toast.replaySaved': 'Replay saved',
  'toast.snapshotLoaded': 'Snapshot loaded',
  'toast.copied': 'Copied to clipboard',

  // ---- Toast: co-op / snapshots / replays (Game.ts) ----
  'toast.coopOff': 'Co-op: OFF',
  'toast.coopOn': 'Co-op: ON — God Player activated!',
  'toast.spectateOff': 'Supervise: OFF',
  'toast.spectateOn': 'Supervise: single ON — God AI fights as Player 1!',
  'toast.spectateDualOn': 'Supervise: DUAL ON — God AI drives both Player 1 and Player 2!',
  'toast.battleSpeed': 'Battle speed: ×{speed}',
  'toast.keyBindingsPaused': 'Key bindings are available when the game is paused',
  'toast.rewindActivated': 'Time Box: time rewind!',
  'toast.replayUnsupported': 'Replay format not supported by this version',
  'toast.replayVersionMismatch': 'Recorded on v{version} — playback may desync',
  'toast.replayEscExit': 'REPLAY — Esc exit',
  'toast.replayFinished': 'Replay finished',
  'toast.replayDeleted': 'Replay deleted',
  'toast.favoritesFull': 'Favorites are full — unfavorite some replays first',
  'toast.noReplayExport': 'No replay to export',
  'toast.replayImported': 'Imported: Stage {stage} — {name}',
  'toast.replayReadError': 'Failed to read replay file',
  'toast.replayExported': 'Exported: {filename}',
  'toast.replayParseError': 'Failed to parse replay: {error}',
  'toast.snapshotSaved': 'Snapshot saved — Stage {stage} · {name}',
  'toast.perfModeOn': 'Performance Mode: ON',
  'toast.perfModeOff': 'Performance Mode: OFF (Quality)',
  'toast.snapshotFull': 'Manual slots full ({n}/{n}) — delete old snapshots in the Browser',

  // ---- Snapshot Browser ----
  'browser.snapshot.title': 'SNAPSHOT BROWSER',
  'browser.snapshot.close': 'Close',
  'browser.snapshot.empty': 'No snapshots yet — play a stage, pause, or press Alt+S to save one.',
  'browser.snapshot.noPreview': 'NO PREVIEW',
  'browser.snapshot.load': 'LOAD',
  'browser.snapshot.delete': 'DELETE',
  'browser.snapshot.confirm': 'SURE?',
  'browser.snapshot.filter.all': 'ALL',
  'browser.snapshot.filter.manual': 'MANUAL',
  'browser.snapshot.filter.pause': 'PAUSE',
  'browser.snapshot.filter.stage-start': 'STAGE START',
  'browser.snapshot.filter.auto': 'AUTO',
  'browser.snapshot.emptyFiltered': 'No {label} snapshots — pick another filter.',
  'browser.snapshot.emptyNoMatch': 'No snapshots match this filter.',
  'browser.snapshot.info.lives': 'Lives',
  'browser.snapshot.info.star': 'Star level',
  'browser.snapshot.info.hp': 'HP',
  'browser.snapshot.info.score': 'Score',
  'browser.snapshot.info.kills': 'Kills',
  'browser.snapshot.info.enemies': 'Enemies remaining',
  'browser.snapshot.info.playtime': 'Play time',
  'browser.snapshot.info.commander': 'Commander on field',

  // ---- Replay Browser ----
  'browser.replay.title': 'REPLAY BROWSER',
  'browser.replay.close': 'Close',
  'browser.replay.empty': 'No replays yet — finish a stage (win or lose) to record one.',
  'browser.replay.dropHint': 'Drop a .replay file here',
  'browser.replay.noPreview': 'NO PREVIEW',
  'browser.replay.play': 'PLAY',
  'browser.replay.favOn': '★ FAV',
  'browser.replay.favOff': '☆ FAV',
  'browser.replay.delete': 'DELETE',
  'browser.replay.confirm': 'SURE?',
  'browser.replay.export': 'Export',
  'browser.replay.exportTitle': 'Export .replay',
  'browser.replay.import': 'Import',
  'browser.replay.info.score': 'Score',
  'browser.replay.info.kills': 'Kills',
  'browser.replay.info.star': 'Player level',
  'browser.replay.info.lives': 'Lives left',
  'browser.replay.info.duration': 'Duration',
  'browser.replay.info.coop': 'Co-op (God AI)',
  'browser.replay.info.spectate': 'Supervise (God AI as Player 1)',
  'browser.replay.info.fav': 'Favorited',
  'browser.replay.filter.all': 'ALL',
  'browser.replay.filter.clear': 'CLEAR',
  'browser.replay.filter.base': 'BASE DOWN',
  'browser.replay.filter.died': 'DIED',
  'browser.replay.filter.timeout': 'TIMEOUT',
  'browser.replay.filter.favorite': 'FAV ★',
  'browser.replay.emptyFiltered': 'No {label} replays — pick another filter.',
  'browser.replay.emptyNoMatch': 'No replays match this filter.',

  // ---- Replay Controller (in-playback video controls) ----
  'replay.ctrl.replayAgain': '↻ REPLAY',
  'replay.ctrl.backToMenu': '✕ MENU',
  'replay.endStage': 'Stage {n}: {name}',
  'replay.result.victory': 'VICTORY',
  'replay.result.defeat': 'DEFEAT',
  'replay.detail.score': 'Score',
  'replay.detail.kills': 'Kills',

  // ---- Control Center (developer sidebar) ----
  'cc.title': 'CONTROL CENTER',
  'cc.section.snapshots': 'SNAPSHOT MANAGER',
  'cc.section.replays': 'REPLAYS',
  'cc.section.gameplay': 'GAMEPLAY',
  'cc.section.display': 'DISPLAY',
  'cc.section.developer': 'DEVELOPER',
  'cc.language': 'Language',
  'cc.save': 'Save Snapshot Now',
  'cc.snapshotBrowser': 'Snapshot Browser',
  'cc.replayBrowser': 'Replay Browser',
  'cc.openLocalReplay': 'Open Local Replay',
  'cc.keyBindings': 'Key Bindings',
  'cc.lieBackWin': 'Lie-Back Win',
  'cc.spectate': 'Supervise',
  'cc.theme': 'Theme',
  'cc.fullscreen': 'Fullscreen',
  'cc.perfMode': 'Performance Mode',
  'cc.debugOverlay': 'Debug Overlay',
  'cc.noSnapshots': 'No snapshots',
  'cc.noReplays': 'No replays',
  // ---- Control Center dynamic status lines (update()) ----
  'cc.counts.fmt': '{total} snapshots · manual {manual}/{limit}',
  'cc.replays.fmt': '{total} replays',
  'cc.replays.fmtFav': '{total} replays · ★ {fav}',
  'cc.gameplay.inRun': '{difficulty} · Stage {n} · {name}',
  'cc.gameplay.menu': '{difficulty} · {theme}',
  // ---- Control Center button tooltips (title=) ----
  'cc.titleCollapse': 'Collapse',
  'cc.titleLanguage': 'Switch language',
  'cc.titleTheme': 'Switch theme (Alt+T) — click to pick',
  'cc.titleCoop': 'Toggle Lie-Back-Win-Mode (God AI co-op)',
  'cc.titleSpectate': 'Cycle Supervise Mode: OFF / single (x1) / dual (x2)',
  'cc.titleFullscreen': 'Toggle fullscreen mode (Alt+F)',
  'cc.titlePerfMode': 'Toggle Performance Mode (DPR cap + render FPS cap)',
  'cc.titleDebug': 'Toggle the Performance Observatory debug HUD',
  'perf.copyReport': 'Copy report',
}
