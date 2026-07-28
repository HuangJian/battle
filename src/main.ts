import './styles/main.css'
import './styles/replay-controller.css'
import { Game } from './game/Game'

const app = document.getElementById('app')
if (!app) throw new Error('#app element not found')

// Create game — PresentationLayer builds the HTML structure inside #app
const game = new Game(app)
game.start()

// Handle visibility — pause when tab is hidden
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    if (game.world.state === 'playing') {
      game.simulation.togglePause()
    }
  }
})

// Prevent context menu on canvas
const canvas = game.presentation.ui.canvas
canvas.addEventListener('contextmenu', (e) => e.preventDefault())
