import { StrictMode, Component, type ReactNode, type ErrorInfo } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }
  render() {
    const { error } = this.state
    if (error) {
      return (
        <div style={{ background: '#1a1a1a', color: '#f87171', padding: 32, fontFamily: 'monospace', fontSize: 14 }}>
          <strong>Error al iniciar la app:</strong>
          <pre style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}>{error.message}{'\n\n'}{error.stack}</pre>
          <button onClick={() => window.location.reload()} style={{ marginTop: 16, padding: '6px 16px', cursor: 'pointer' }}>
            Recargar
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
