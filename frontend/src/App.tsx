import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Overview } from './pages/Overview'
import { Ingest } from './pages/Ingest'
import { Signals } from './pages/Signals'
import { Positions } from './pages/Positions'
import { Candidates } from './pages/Candidates'
import { DailySummary } from './pages/DailySummary'
import { DataHealth } from './pages/DataHealth'
import { KalshiMarkets } from './pages/KalshiMarkets'
import { MLBTeamContext } from './pages/MLBTeamContext'
import { Journal } from './pages/Journal'

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="/ingest" element={<Ingest />} />
        <Route path="/signals" element={<Signals />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/candidates" element={<Candidates />} />
        <Route path="/journal" element={<Journal />} />
        <Route path="/summary" element={<DailySummary />} />
        <Route path="/health" element={<DataHealth />} />
        <Route path="/kalshi" element={<KalshiMarkets />} />
        <Route path="/mlb-context" element={<MLBTeamContext />} />
      </Route>
    </Routes>
  )
}
