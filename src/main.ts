import './styles/main.css'
import { Game } from './game/Game'

const app = document.getElementById('app')
if (!app) throw new Error('#app element not found')

// Create game container
const container = document.createElement('div')
container.className = 'game-container'

// Create canvas
const canvas = document.createElement('canvas')
container.appendChild(canvas)
app.appendChild(container)

// Create footer
const footer = document.createElement('div')
footer.className = 'footer'
footer.innerHTML = `
  <span>WASD</span>/Arrows Move &nbsp;·&nbsp;
  <span>Space</span> Fire &nbsp;·&nbsp;
  <span>P</span> Pause &nbsp;·&nbsp;
  <span>R</span> Reset &nbsp;·&nbsp;
  <span>Enter</span> Start
`
app.appendChild(footer)

// Create game
const game = new Game(canvas)
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
canvas.addEventListener('contextmenu', (e) => e.preventDefault())
