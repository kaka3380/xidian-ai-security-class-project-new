import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { scientistApi, type ObsidianVault } from '@/lib/api'

export function KnowledgeBaseWorkspace() {
  const [kind, setKind] = useState<ObsidianVault['kind']>('academic')
  const [selected, setSelected] = useState('')
  const [filter, setFilter] = useState('')
  const [academicPath, setAcademicPath] = useState('')
  const [socialPath, setSocialPath] = useState('')
  const [editingPaths, setEditingPaths] = useState(false)
  const queryClient = useQueryClient()
  const vaults = useQuery({ queryKey: ['vaults'], queryFn: async () => (await scientistApi.vaultsStatus()).data.vaults })
  const notes = useQuery({ queryKey: ['vault-notes', kind], queryFn: async () => (await scientistApi.vaultNotesByKind(kind)).data.notes })
  const note = useQuery({ queryKey: ['vault-note', kind, selected], enabled: !!selected, queryFn: async () => (await scientistApi.vaultNoteByKind(kind, selected)).data })
  useEffect(() => {
    if (vaults.data && !editingPaths) {
      setAcademicPath(vaults.data.find(vault => vault.kind === 'academic')?.path || '')
      setSocialPath(vaults.data.find(vault => vault.kind === 'social')?.path || '')
    }
  }, [vaults.data, editingPaths])
  const sync = useMutation({ mutationFn: scientistApi.syncVaults, onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['vaults'] }); queryClient.invalidateQueries({ queryKey: ['vault-notes'] }); toast.success('归档同步完成') }, onError: () => toast.error('归档同步失败，请检查目录和04日志') })
  const configure = useMutation({ mutationFn: () => scientistApi.configureVaults(academicPath, socialPath), onSuccess: () => { setSelected(''); setEditingPaths(false); queryClient.invalidateQueries({ queryKey: ['vaults'] }); queryClient.invalidateQueries({ queryKey: ['vault-notes'] }); toast.success('目录已保存') }, onError: () => toast.error('保存失败，请确认目录可访问') })
  const visible = (notes.data || []).filter(item => `${item.title} ${item.path}`.toLowerCase().includes(filter.toLowerCase()))
  return <div className="mx-auto max-w-6xl space-y-5">
    <div className="flex flex-wrap justify-between gap-3"><div><h1 className="text-2xl font-semibold">知识库</h1><p className="mt-3 text-sm text-zinc-400">查看学术来源卡、社交情报与分析报告。</p></div><div className="flex gap-2"><Button variant="outline" onClick={() => setEditingPaths(value => !value)}>目录设置</Button><Button onClick={() => sync.mutate()} disabled={sync.isPending}><RefreshCw className={sync.isPending ? 'animate-spin' : ''} />同步归档</Button></div></div>
    {editingPaths && <section className="space-y-3 rounded-xl border border-white/10 bg-[#202020] p-4"><label className="block text-sm">学术目录<input value={academicPath} onChange={event => setAcademicPath(event.target.value)} className="mt-2 w-full rounded-md border border-white/10 bg-[#171717] p-2" /></label><label className="block text-sm">社交目录<input value={socialPath} onChange={event => setSocialPath(event.target.value)} className="mt-2 w-full rounded-md border border-white/10 bg-[#171717] p-2" /></label><Button onClick={() => configure.mutate()} disabled={configure.isPending}>保存目录</Button></section>}
    <div className="flex flex-wrap gap-2">{(['academic', 'social'] as const).map(value => <Button key={value} variant={kind === value ? 'default' : 'outline'} onClick={() => { setKind(value); setSelected(''); setFilter('') }}>{value === 'academic' ? '学术资料' : '社交与分析报告'} · {vaults.data?.find(vault => vault.kind === value)?.note_count ?? 0}</Button>)}</div>
    <input aria-label="搜索知识库文档" placeholder="搜索标题或路径" value={filter} onChange={event => setFilter(event.target.value)} className="w-full rounded-md border border-white/10 bg-[#222] px-3 py-2 text-sm" />
    <div className="grid min-h-[500px] gap-4 lg:grid-cols-[340px_minmax(0,1fr)]"><div className="max-h-[650px] overflow-auto rounded-xl border border-white/10 bg-[#202020] p-2">{notes.isPending ? <p className="p-4 text-sm text-zinc-500">正在加载…</p> : notes.isError ? <p role="alert" className="p-4 text-sm text-rose-300">目录读取失败，请检查知识库配置。</p> : !visible.length ? <p className="p-4 text-sm text-zinc-500">暂无匹配文档</p> : visible.map(item => <button key={item.path} onClick={() => setSelected(item.path)} className={`mb-1 w-full rounded-md p-3 text-left ${selected === item.path ? 'bg-cyan-400/10 text-cyan-200' : 'hover:bg-white/5'}`}><span className="block text-sm">{item.title}</span><span className="mt-2 block break-all text-xs text-zinc-500">{item.path}</span></button>)}</div><section className="min-w-0 rounded-xl border border-white/10 bg-[#202020] p-5">{!selected ? <p className="text-sm text-zinc-500">选择一份文档查看原文</p> : note.isPending ? <p>正在读取…</p> : note.isError ? <p role="alert" className="text-rose-300">文档加载失败</p> : <><h2 className="mb-4 break-all text-sm text-cyan-200">{selected}</h2><pre className="max-h-[650px] overflow-auto whitespace-pre-wrap break-words font-sans text-sm leading-7 text-zinc-300">{note.data?.content}</pre></>}</section></div>
  </div>
}
