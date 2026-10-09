import { Eye, EyeOff, Loader2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Slider } from '@/components/ui/slider'
import { ModelPicker } from './ModelPicker'
import { getModelSettings, resetModelSettings, testModelSettings, updateModelSettings } from '@/services/settings'
import type { ModelCategory, ModelTestResult } from '@/types/api'

interface ModelConfigFormProps {
  category: ModelCategory
  onChanged?: () => void
}

/**
 * 加载并编辑单个模型类别的配置。
 *
 * @param props 模型类别和配置变更回调
 * @returns 类别专属表单
 */
export function ModelConfigForm({ category, onChanged }: ModelConfigFormProps) {
  const { t } = useTranslation('settings')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState<ModelTestResult | null>(null)
  const [provider, setProvider] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [temperature, setTemperature] = useState(0.3)
  const [maxTokens, setMaxTokens] = useState(4096)
  const [dimension, setDimension] = useState(1536)
  const [batchSize, setBatchSize] = useState(32)
  const [providerOptions, setProviderOptions] = useState<Array<{ id: string; display_name: string; default_base_url: string | null }>>([])

  useEffect(() => {
    getModelSettings(category)
      .then((data) => {
        setProvider(data.provider)
        setBaseUrl(data.base_url)
        setModel(data.model)
        setProviderOptions(data.provider_options)
        setTemperature(Number(data.parameters.temperature ?? 0.3))
        setMaxTokens(Number(data.parameters.max_tokens ?? 4096))
        setDimension(Number(data.parameters.dimension ?? 1536))
        setBatchSize(Number(data.parameters.batch_size ?? 32))
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }, [category])

  const selectedProvider = useMemo(() => providerOptions.find((item) => item.id === provider), [provider, providerOptions])

  function payload() {
    return {
      provider,
      base_url: baseUrl,
      ...(apiKey ? { api_key: apiKey } : {}),
      model,
      ...(category === 'llm' ? { temperature, max_tokens: maxTokens } : { dimension, batch_size: batchSize }),
    }
  }

  async function handleSave() {
    setSaving(true); setError('')
    try {
      await updateModelSettings(category, payload())
      setApiKey('')
      onChanged?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally { setSaving(false) }
  }

  async function handleTest() {
    setTesting(true); setError(''); setTestResult(null)
    try {
      setTestResult(await testModelSettings(category, payload()))
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally { setTesting(false) }
  }

  async function handleReset() {
    setError('')
    await resetModelSettings(category)
    setApiKey('')
    onChanged?.()
  }

  if (loading) return <div className="p-6 text-sm text-muted-foreground">{t('field.modelsLoading')}</div>

  return (
    <form
      autoComplete="off"
      data-form-type="other"
      className="bg-card rounded-lg border border-border p-6 space-y-5"
      onSubmit={(event) => event.preventDefault()}
    >
      {error && <div className="rounded-md bg-error/10 px-3 py-2 text-sm text-error">{error}</div>}
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-medium">{t('field.provider')}</label>
          <Select value={provider} onValueChange={(value) => { setProvider(value); setBaseUrl(providerOptions.find((item) => item.id === value)?.default_base_url ?? '') }}>
            <SelectTrigger><SelectValue placeholder={t('field.providerPlaceholder')} /></SelectTrigger>
            <SelectContent>{providerOptions.map((item) => <SelectItem key={item.id} value={item.id}>{item.display_name}</SelectItem>)}</SelectContent>
          </Select>
        </div>
        <ModelPicker
          category={category}
          value={model}
          onChange={(value, selectedModel) => {
            setModel(value)
            if (category === 'embedding' && selectedModel?.dimension) {
              setDimension(selectedModel.dimension)
            }
          }}
          provider={provider}
          baseUrl={baseUrl}
          apiKey={apiKey}
        />
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium">{t('field.apiKey')}</label>
        <div className="relative">
          <Input
            type={showKey ? 'text' : 'password'}
            name={`${category}_api_key`}
            value={apiKey}
            autoComplete="new-password"
            spellCheck={false}
            data-lpignore="true"
            data-1p-ignore="true"
            onChange={(event) => setApiKey(event.target.value)}
            placeholder={apiKey ? '' : '****'}
            className="pr-10"
          />
          <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2" onClick={() => setShowKey((value) => !value)}>
            {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
      </div>
      <div>
        <label className="mb-1 block text-sm font-medium">Base URL</label>
        <Input value={baseUrl} autoComplete="off" spellCheck={false} onChange={(event) => setBaseUrl(event.target.value)} placeholder={selectedProvider?.default_base_url ?? ''} />
      </div>
      {category === 'llm' ? (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_180px]">
          <div>
            <label className="mb-1 block text-sm font-medium">Temperature：{temperature.toFixed(2)}</label>
            <Slider value={[temperature]} onValueChange={([value]) => setTemperature(value)} min={0} max={2} step={0.1} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">Max Tokens</label>
            <Input type="number" value={maxTokens} min={1} onChange={(event) => setMaxTokens(Number(event.target.value) || 1)} />
          </div>
        </div>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          <div>
            <label className="mb-1 block text-sm font-medium">{t('field.dimension')}</label>
            <Input type="number" value={dimension} min={1} onChange={(event) => setDimension(Number(event.target.value) || 1)} />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">{t('field.batchSize')}</label>
            <Input type="number" value={batchSize} min={1} onChange={(event) => setBatchSize(Number(event.target.value) || 1)} />
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-3">
        <Button type="button" onClick={() => void handleSave()} disabled={saving}>{saving && <Loader2 className="h-4 w-4 animate-spin" />}{t('action.save')}</Button>
        <Button type="button" variant="secondary" onClick={() => void handleTest()} disabled={testing}>{testing && <Loader2 className="h-4 w-4 animate-spin" />}{t('action.test')}</Button>
        <Button type="button" variant="secondary" onClick={() => void handleReset()}>{t('action.reset')}</Button>
      </div>
      {testResult && (
        <p className={testResult.success ? 'text-sm text-success' : 'text-sm text-error'}>
          {testResult.success
            ? '连接成功'
            : `${testResult.error || '测试失败'}${testResult.dimension ? `（实际维度：${testResult.dimension}）` : ''}`}
        </p>
      )}
    </form>
  )
}
