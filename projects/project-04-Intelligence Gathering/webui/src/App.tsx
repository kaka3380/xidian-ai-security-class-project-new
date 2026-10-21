import { useState } from 'react'
import { BarChart3, BookOpen, Database, FileText, FlaskConical, GraduationCap, KeyRound, LayoutDashboard, Search } from 'lucide-react'
import { Toaster } from 'sonner'
import { CrawlerConfigPanel } from '@/components/config/CrawlerConfigPanel'
import { MainContent } from '@/components/layout/MainContent'
import { DataExplorer } from '@/components/data/DataExplorer'
import { AccountSettings } from '@/components/config/AccountSettings'
import { AnalysisWorkspace } from '@/components/analysis/AnalysisWorkspace'
import { ScientistWorkspace } from '@/components/scientist/ScientistWorkspace'
import { KnowledgeBaseWorkspace } from '@/components/knowledge/KnowledgeBaseWorkspace'
import { DeepTutorWorkspace } from '@/components/learning/DeepTutorWorkspace'
import { EnvironmentCheck, isEnvChecked } from '@/components/env/EnvironmentCheck'
import { LicenseDisclaimer, isLicenseAccepted } from '@/components/license/LicenseDisclaimer'

type View = 'new' | 'library' | 'logs' | 'accounts' | 'analysis' | 'scientist' | 'knowledge' | 'learning'

function NavItem({ icon: Icon, label, active, onClick }: { icon: typeof Search; label: string; active?: boolean; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={`relative w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors ${active ? 'bg-cyan-400/10 text-cyan-100 shadow-[inset_3px_0_0_#22d3ee]' : 'text-zinc-400 hover:bg-white/5 hover:text-zinc-200'}`}><Icon className={`w-4 h-4 ${active ? 'text-cyan-300' : ''}`} />{label}</button>
}

function App() {
  const [licenseAccepted, setLicenseAccepted] = useState(() => isLicenseAccepted())
  const [envChecked, setEnvChecked] = useState(() => isEnvChecked())
  const [view, setView] = useState<View>(() => new URLSearchParams(window.location.search).get('view') === 'learning' ? 'learning' : 'new')
  const [showDisclaimer, setShowDisclaimer] = useState(false)

  return <div className="h-screen overflow-hidden bg-[#151515] text-zinc-100 flex">
    {(!licenseAccepted || showDisclaimer) && <LicenseDisclaimer onAccept={() => { setLicenseAccepted(true); setShowDisclaimer(false) }} />}
    {licenseAccepted && !showDisclaimer && !envChecked && <EnvironmentCheck onCheckComplete={() => setEnvChecked(true)} />}

    <aside className="w-[236px] shrink-0 bg-[#1c1c1c] border-r border-white/[0.06] flex flex-col p-3">
      <div className="flex items-center gap-2 px-2 py-2 mb-3"><span className="w-5 h-5 rounded-full bg-gradient-to-br from-cyan-300 via-pink-400 to-orange-300" /><span className="font-semibold text-sm">人工智能安全平台</span></div>
      <nav className="space-y-1">
        <NavItem icon={FileText} label="新任务" active={view === 'new'} onClick={() => setView('new')} />
        <NavItem icon={BookOpen} label="采集结果" active={view === 'library'} onClick={() => setView('library')} />
        <NavItem icon={LayoutDashboard} label="运行日志" active={view === 'logs'} onClick={() => setView('logs')} />
        <NavItem icon={KeyRound} label="账号设置" active={view === 'accounts'} onClick={() => setView('accounts')} />
        <NavItem icon={BarChart3} label="内容分析" active={view === 'analysis'} onClick={() => setView('analysis')} />
        <NavItem icon={BookOpen} label="知识库" active={view === 'knowledge'} onClick={() => setView('knowledge')} />
        <NavItem icon={GraduationCap} label="学习问答" active={view === 'learning'} onClick={() => setView('learning')} />
        <NavItem icon={FlaskConical} label="科研自动化" active={view === 'scientist'} onClick={() => setView('scientist')} />
      </nav>
      <div className="flex-1" />
      <button type="button" onClick={() => setShowDisclaimer(true)} className="text-left px-3 py-2 text-xs text-zinc-500 hover:text-zinc-300">使用说明与许可</button>
    </aside>

    <main className="flex-1 min-w-0 bg-[#171717] relative overflow-auto">
      <header className="h-14 border-b border-white/[0.06] flex items-center px-6 justify-between sticky top-0 bg-[#171717]/95 backdrop-blur z-10"><div className="flex items-center gap-3 text-sm"><span className="text-zinc-500">工作区</span><span className="text-zinc-700">/</span><span>{view === 'new' ? '新任务' : view === 'library' ? '采集结果' : view === 'logs' ? '运行日志' : view === 'accounts' ? '账号设置' : view === 'analysis' ? '内容分析' : view === 'knowledge' ? '知识库' : view === 'learning' ? '学习问答' : '科研自动化'}</span></div><div className="flex items-center gap-2 text-xs text-zinc-500"><span className="w-2 h-2 rounded-full bg-emerald-400" />本地服务正常</div></header>
      {view === 'new' ? <div className="max-w-5xl mx-auto px-6 py-12">
        <div className="max-w-2xl mx-auto text-center mb-12"><h1 className="text-3xl font-semibold tracking-tight">探索人工智能安全研究</h1><p className="mt-3 text-sm text-zinc-500">从公开平台收集 AI 安全领域的研究动态、实践经验与风险信号。</p></div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-8"><button type="button" onClick={() => document.getElementById('crawler-config')?.scrollIntoView({ behavior: 'smooth' })} className="text-left rounded-xl bg-[#202020] border border-white/[0.06] hover:border-cyan-400/40 p-5 transition-colors"><div className="flex items-center justify-between"><div><h2 className="font-medium">搜索安全研究内容</h2><p className="mt-2 text-xs leading-5 text-zinc-500">输入关键词，追踪 AI 安全研究、工程实践与行业讨论。</p></div><Search className="w-5 h-5 text-cyan-300" /></div></button><button type="button" onClick={() => setView('library')} className="text-left rounded-xl bg-[#202020] border border-white/[0.06] hover:border-cyan-400/40 p-5 transition-colors"><div className="flex items-center justify-between"><div><h2 className="font-medium">查看采集结果</h2><p className="mt-2 text-xs leading-5 text-zinc-500">浏览、预览和下载已经保存的安全内容，作为后续分析输入。</p></div><Database className="w-5 h-5 text-violet-300" /></div></button></div>
        <div id="crawler-config"><CrawlerConfigPanel /></div>
      </div> : view === 'library' ? <div className="h-[calc(100vh-56px)] p-6"><DataExplorer /></div> : view === 'logs' ? <div className="h-[calc(100vh-56px)] p-6"><MainContent /></div> : view === 'accounts' ? <div className="px-6 py-10"><AccountSettings /></div> : view === 'analysis' ? <div className="px-6 py-10"><AnalysisWorkspace /></div> : view === 'knowledge' ? <div className="px-6 py-10"><KnowledgeBaseWorkspace /></div> : view === 'learning' ? <div className="px-6 py-10"><DeepTutorWorkspace /></div> : <div className="px-6 py-10"><ScientistWorkspace /></div>}
    </main>
    <Toaster position="top-right" toastOptions={{ style: { background: '#252525', color: '#fff', border: '1px solid #444' } }} />
  </div>
}

export default App
