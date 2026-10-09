import api from './client'
import type { AvailableModels, LLMSettings, LLMTestResult, ModelCategory, ModelConfigList, ModelSettings, ModelTestResult } from '@/types/api'

export async function getLLMSettings(): Promise<LLMSettings> {
  return api.get('settings/llm').json<LLMSettings>()
}

export async function updateLLMSettings(data: {
  provider?: string
  base_url?: string
  api_key?: string
  model?: string
  temperature?: number
  max_tokens?: number
}): Promise<{ updated: boolean; settings: LLMSettings }> {
  return api.put('settings/llm', { json: data }).json()
}

export async function resetLLMSettings(): Promise<{ reset: boolean; message: string }> {
  return api.delete('settings/llm').json()
}

export async function testLLMConnection(data?: {
  provider?: string
  base_url?: string
  api_key?: string
  model?: string
  temperature?: number
  max_tokens?: number
}): Promise<LLMTestResult> {
  return api.post('settings/llm/test', { json: data || {} }).json<LLMTestResult>()
}

export async function getModelConfigList(): Promise<ModelConfigList> {
  return api.get('settings/models').json<ModelConfigList>()
}

export async function getModelSettings(category: ModelCategory): Promise<ModelSettings> {
  return api.get(`settings/models/${category}`).json<ModelSettings>()
}

export async function updateModelSettings(category: ModelCategory, data: Record<string, unknown>): Promise<{ updated: boolean; settings: ModelSettings }> {
  return api.put(`settings/models/${category}`, { json: data }).json()
}

export async function resetModelSettings(category: ModelCategory): Promise<{ reset: boolean; message: string }> {
  return api.delete(`settings/models/${category}`).json()
}

export async function testModelSettings(category: ModelCategory, data: Record<string, unknown>): Promise<ModelTestResult> {
  return api.post(`settings/models/${category}/test`, { json: data }).json<ModelTestResult>()
}

export async function getAvailableModels(category: ModelCategory, data: Record<string, unknown>): Promise<AvailableModels> {
  return api.post(`settings/models/${category}/available`, { json: data }).json<AvailableModels>()
}
