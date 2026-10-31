import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { dataApi } from '@/lib/api'

export function DataExplorer() {
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState('')
  const queryClient = useQueryClient()
  const files = useQuery({ queryKey: ['data-files'], queryFn: async () => (await dataApi.getFiles()).data.files })
  const preview = useQuery({ queryKey: ['data-preview', selected], enabled: !!selected, queryFn: async () => (await dataApi.getFileContent(selected)).data })
  const remove = useMutation({
    mutationFn: dataApi.deleteFile,
    onSuccess: () => { setSelected(''); queryClient.invalidateQueries({ queryKey: ['data-files'] }); toast.success('文件已删除') },
    onError: () => toast.error('删除失败，请检查文件状态'),
  })
  const rows = preview.data ? (Array.isArray(preview.data.data) ? preview.data.data : [preview.data.data]) : []
  const columns = [...new Set(rows.flatMap(row => Object.keys(row)))]
  const visible = (files.data || []).filter(file => file.path.toLowerCase().includes(filter.toLowerCase()))
  return <div className="flex h-full min-h-[500px] flex-col gap-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-xl font-semibold">采集结果</h1><p className="mt-2 text-sm text-zinc-500">浏览、预览和下载本地采集文件及分析结果。</p></div><Button variant="outline" onClick={() => files.refetch()}><RefreshCw />刷新</Button></div>
    <input aria-label="搜索文件" value={filter} onChange={event => setFilter(event.target.value)} placeholder="搜索文件名或路径" className="rounded-md border border-white/10 bg-[#222] px-3 py-2 text-sm" />
    {files.isError && <p role="alert" className="text-sm text-rose-300">文件列表加载失败，请重试。</p>}
    <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
      <div className="overflow-auto rounded-xl border border-white/10 bg-[#202020] p-2">
        {files.isPending ? <p className="p-4 text-sm text-zinc-400">正在加载…</p> : !visible.length ? <p className="p-4 text-sm text-zinc-500">暂无匹配文件</p> : visible.map(file => <button key={file.path} onClick={() => setSelected(file.path)} className={`mb-1 w-full rounded-md p-3 text-left ${selected === file.path ? 'bg-cyan-400/10 text-cyan-200' : 'hover:bg-white/5'}`}><span className="block break-all text-sm">{file.name}</span><span className="mt-2 block break-all text-xs text-zinc-500">{file.path} · {(file.size / 1024).toFixed(1)} KB</span></button>)}
      </div>
      <section className="min-w-0 overflow-auto rounded-xl border border-white/10 bg-[#202020] p-4">
        {!selected ? <p className="text-sm text-zinc-500">选择一个文件以预览</p> : <><div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="break-all text-sm font-medium">{selected}</h2><div className="flex gap-2"><a href={dataApi.getDownloadUrl(selected)} className="inline-flex items-center gap-2 px-2 py-1 text-sm text-cyan-200"><Download className="h-4 w-4" />下载</a><Button variant="ghost" disabled={remove.isPending} onClick={() => { if (window.confirm(`确认删除文件 ${selected}？`)) remove.mutate(selected) }}><Trash2 />删除</Button></div></div>
          {preview.isPending ? <p>正在读取…</p> : preview.isError ? <p role="alert" className="text-rose-300">预览加载失败</p> : <><p className="mb-3 text-xs text-zinc-500">共 {preview.data?.total ?? 0} 条，展示前 {rows.length} 条</p><div className="overflow-auto"><table className="w-full text-left text-xs"><thead><tr>{columns.map(column => <th key={column} className="border-b border-white/10 p-2 text-zinc-400">{column}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={index}>{columns.map(column => <td key={column} className="max-w-md border-b border-white/5 p-2 align-top"><div className="max-h-36 min-w-28 overflow-auto whitespace-pre-wrap break-words">{typeof row[column] === 'object' ? JSON.stringify(row[column]) : String(row[column] ?? '')}</div></td>)}</tr>)}</tbody></table></div></>}
        </>}
      </section>
    </div>
  </div>
}
