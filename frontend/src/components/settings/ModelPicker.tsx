import { ChevronDown, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { getAvailableModels } from '@/services/settings'
import type { AvailableModel, ModelCategory } from '@/types/api'

interface ModelPickerProps {
  category: ModelCategory
  value: string
  onChange: (value: string, model?: AvailableModel) => void
  provider: string
  baseUrl: string
  apiKey: string
}

/**
 * 渲染模型输入和供应商模型获取操作。
 *
 * @param props 当前类别、草稿配置和值变更回调
 * @returns 可手工输入并支持悬浮模型列表的输入组件
 */
export function ModelPicker({ category, value, onChange, provider, baseUrl, apiKey }: ModelPickerProps) {
  const { t } = useTranslation('settings')
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [models, setModels] = useState<AvailableModel[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')

  async function handleFetch() {
    setLoading(true)
    setError('')
    try {
      const result = await getAvailableModels(category, {
        provider,
        base_url: baseUrl,
        ...(apiKey ? { api_key: apiKey } : {}),
        model: value,
      })
      setModels(result.models)
      setLoaded(true)
      setOpen(true)
      if (!result.models.length) setError(t('field.modelsEmpty'))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('field.modelsFailed'))
      setOpen(true)
    } finally {
      setLoading(false)
    }
  }

  function handleSelect(model: AvailableModel) {
    onChange(model.id, model)
    setOpen(false)
  }

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium">{t('field.model')}</label>
      <div className="flex gap-2">
        <div className="relative min-w-0 flex-1">
          <Input
            name={`${category}_model_id`}
            value={value}
            autoComplete="off"
            spellCheck={false}
            data-lpignore="true"
            data-1p-ignore="true"
            onChange={(event) => onChange(event.target.value)}
            placeholder={t('field.modelPlaceholder')}
            className="pr-10"
          />
          <Popover
            open={open}
            onOpenChange={(nextOpen) => {
              if (nextOpen && !loaded && !loading) {
                void handleFetch()
                return
              }
              setOpen(nextOpen)
            }}
          >
            <PopoverTrigger asChild>
              <button
                type="button"
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground transition-colors hover:text-foreground"
                aria-label={t('field.getModels')}
              >
                {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <ChevronDown className="h-4 w-4" />}
              </button>
            </PopoverTrigger>
            <PopoverContent align="start" className="max-h-64 overflow-auto">
              {error && <p className="px-3 py-2 text-xs text-error">{error}</p>}
              {!error && models.length === 0 && <p className="px-3 py-2 text-xs text-muted-foreground">{t('field.modelsEmpty')}</p>}
              {models.map((model) => (
                <button
                  key={model.id}
                  type="button"
                  role="option"
                  aria-selected={model.id === value}
                  className="flex w-full items-center justify-between gap-3 rounded-sm px-3 py-2 text-left text-sm hover:bg-muted"
                  onClick={() => handleSelect(model)}
                >
                  <span className="min-w-0 truncate font-mono">{model.id}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{model.capability_source}</span>
                </button>
              ))}
            </PopoverContent>
          </Popover>
        </div>
        <Button type="button" variant="secondary" onClick={() => void handleFetch()} disabled={loading}>
          <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          {t('field.getModels')}
        </Button>
      </div>
      {error && !open && <p className="text-xs text-error">{error}</p>}
    </div>
  )
}
