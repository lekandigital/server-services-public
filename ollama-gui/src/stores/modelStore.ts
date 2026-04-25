import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useLocalStorage } from '@vueuse/core'
import type { OllamaModel } from '@/types/ollama'
import { listModels } from '@/services/ollama'

interface ModelMeta {
  rank: number
  stars: string
  reasoning: boolean
}

const MODEL_RANKINGS: Record<string, ModelMeta> = {
  // Tier 1 — Best overall (5 stars)
  'qwen35-claude-opus-abliterated': { rank: 1, stars: '★★★★★', reasoning: true },
  'behemoth-123b': { rank: 2, stars: '★★★★★', reasoning: true },
  'huihui_ai/qwen3-abliterated:30b-a3b': { rank: 3, stars: '★★★★★', reasoning: true },
  'qwen3-uncensored': { rank: 4, stars: '★★★★★', reasoning: true },
  // Tier 2 — Strong (4 stars)
  'huihui_ai/gemma3-abliterated:27b': { rank: 5, stars: '★★★★☆', reasoning: false },
  'gemma3-uncensored': { rank: 6, stars: '★★★★☆', reasoning: false },
  'huihui_ai/phi4-abliterated': { rank: 7, stars: '★★★★☆', reasoning: true },
  'phi4-uncensored': { rank: 8, stars: '★★★★☆', reasoning: true },
  'huihui_ai/nemotron-abliterated': { rank: 9, stars: '★★★★☆', reasoning: false },
  'mixtral:8x7b': { rank: 10, stars: '★★★★☆', reasoning: false },
  // Tier 3 — Good (3 stars)
  'huihui_ai/glm-4.7-flash-abliterated': { rank: 11, stars: '★★★☆☆', reasoning: false },
  'glm4-uncensored': { rank: 12, stars: '★★★☆☆', reasoning: false },
  'huihui_ai/gpt-oss-abliterated': { rank: 13, stars: '★★★☆☆', reasoning: false },
  'mistral:latest': { rank: 14, stars: '★★★☆☆', reasoning: false },
  'Godmoded/llama3-lexi-uncensored': { rank: 15, stars: '★★★☆☆', reasoning: false },
  // Tier 4 — Basic (2 stars)
  'falcon:7b': { rank: 16, stars: '★★☆☆☆', reasoning: false },
  'Malicus7862/thebloke-luna-ai-llama2-uncensored-gguf': {
    rank: 17,
    stars: '★★☆☆☆',
    reasoning: false,
  },
}

// Models with known reasoning/thinking capability (match by substring)
const REASONING_PATTERNS = [
  'qwen3', 'deepseek-r1', 'deepseek-r2', 'phi4', 'behemoth',
  'claude-opus', 'o1', 'o3', 'o4',
]

function getModelRanking(modelName: string): ModelMeta {
  if (MODEL_RANKINGS[modelName]) return MODEL_RANKINGS[modelName]

  const withoutTag = modelName.replace(/:latest$/, '')
  if (MODEL_RANKINGS[withoutTag]) return MODEL_RANKINGS[withoutTag]

  for (const [key, value] of Object.entries(MODEL_RANKINGS)) {
    const baseName = key.split(':')[0].split('/').pop() || key
    const modelBase = modelName.split(':')[0].split('/').pop() || modelName
    if (
      baseName.toLowerCase().includes(modelBase.toLowerCase()) ||
      modelBase.toLowerCase().includes(baseName.toLowerCase())
    ) {
      return value
    }
  }

  // Check if model matches reasoning patterns even if not in rankings
  const nameLower = modelName.toLowerCase()
  const isReasoning = REASONING_PATTERNS.some((p) => nameLower.includes(p))

  return { rank: 99, stars: '☆☆☆☆☆', reasoning: isReasoning }
}

export interface RankedModel extends OllamaModel {
  ranking: ModelMeta
  displayName: string
}

export const useModelStore = defineStore('models', () => {
  const models = ref<OllamaModel[]>([])
  const currentModel = useLocalStorage('currentModel', 'none')
  const isLoading = ref(false)

  const rankedModels = computed<RankedModel[]>(() => {
    return [...models.value]
      .map((model) => ({
        ...model,
        ranking: getModelRanking(model.name),
        displayName: '',
      }))
      .sort((a, b) => a.ranking.rank - b.ranking.rank)
      .map((model, index) => ({
        ...model,
        displayName: `${index + 1}. ${model.ranking.stars} ${model.ranking.reasoning ? '🧠 ' : ''}${model.name}`,
      }))
  })

  async function refresh() {
    isLoading.value = true
    try {
      const response = await listModels()
      models.value = response.models
    } catch (err) {
      console.error('Failed to fetch models:', err)
    } finally {
      isLoading.value = false
    }
  }

  function formatSize(bytes: number): string {
    const gb = bytes / 1024 / 1024 / 1024
    return gb >= 1 ? `${gb.toFixed(1)} GB` : `${(gb * 1024).toFixed(0)} MB`
  }

  return {
    models,
    currentModel,
    isLoading,
    rankedModels,
    refresh,
    formatSize,
  }
})
