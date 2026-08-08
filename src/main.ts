import './styles/main.css'
import './styles/replay-controller.css'
import { Game } from './game/Game'

const app = document.getElementById('app')
if (!app) throw new Error('#app element not found')

// Create game — PresentationLayer builds the HTML structure inside #app
const game = new Game(app)
game.start()

// Handle visibility — pause live gameplay when tab is hidden.
// Skip during replay playback: PlaybackController manages its own pause state
// (phase field), and calling simulation.togglePause() here would corrupt
// world.state to 'paused', making simulation.tick() a no-op. The replay's
// progress bar would advance (cursor moves) but the world would never update
// (画面不动) — the "pause → switch app → return → click Play" bug.
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (!game.playback && game.world.state === 'playing') {
      game.simulation.togglePause()
    }
  }
})

// Prevent context menu on canvas
const canvas = game.presentation.ui.canvas
canvas.addEventListener('contextmenu', (e) => e.preventDefault())
