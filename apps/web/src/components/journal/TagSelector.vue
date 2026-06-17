<template>
  <div class="tag-selector">
    <div class="selected-tags" v-if="selectedTags.length > 0">
      <span
        v-for="tag in selectedTags"
        :key="tag.id"
        class="tag-pill"
        :style="{ background: tag.color + '22', borderColor: tag.color, color: tag.color }"
      >
        {{ tag.name }}
        <button class="tag-remove" @click="removeTag(tag.id)" :style="{ color: tag.color }">&times;</button>
      </span>
    </div>
    <div class="tag-controls">
      <div class="tag-search">
        <input
          type="text"
          v-model="searchQuery"
          placeholder="搜索或创建标签..."
          class="search-input"
          @focus="showDropdown = true"
          @keydown.enter.prevent="createNewTag"
        />
        <div class="dropdown" v-if="showDropdown && filteredTags.length > 0">
          <div
            v-for="tag in filteredTags"
            :key="tag.id"
            class="dropdown-item"
            :class="{ selected: isSelected(tag.id) }"
            @click="toggleTag(tag)"
          >
            <span class="tag-dot" :style="{ background: tag.color }"></span>
            <span class="tag-name">{{ tag.name }}</span>
            <span class="tag-count">{{ tag.usage_count }}</span>
          </div>
        </div>
        <div class="dropdown" v-else-if="showDropdown && searchQuery && filteredTags.length === 0">
          <div class="dropdown-item create-item" @click="createNewTag">
            <span class="create-icon">+</span>
            <span>创建标签 "{{ searchQuery }}"</span>
          </div>
        </div>
      </div>
      <span class="tag-limit" :class="{ exceeded: selectedTags.length >= 10 }">{{ selectedTags.length }}/10</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import type { TagSummary } from '../../types/api'

const props = defineProps<{
  selectedTags: TagSummary[]
  allTags: TagSummary[]
}>()

const emit = defineEmits<{
  'update:selectedTags': [tags: TagSummary[]]
  'create-tag': [name: string]
}>()

const searchQuery = ref('')
const showDropdown = ref(false)

const filteredTags = computed(() => {
  if (!searchQuery.value) return props.allTags
  const q = searchQuery.value.toLowerCase()
  return props.allTags.filter((t) => t.name.toLowerCase().includes(q))
})

function isSelected(tagId: string): boolean {
  return props.selectedTags.some((t) => t.id === tagId)
}

function toggleTag(tag: TagSummary): void {
  if (isSelected(tag.id)) {
    emit('update:selectedTags', props.selectedTags.filter((t) => t.id !== tag.id))
  } else {
    if (props.selectedTags.length >= 10) return
    emit('update:selectedTags', [...props.selectedTags, tag])
  }
  showDropdown.value = false
  searchQuery.value = ''
}

function removeTag(tagId: string): void {
  emit('update:selectedTags', props.selectedTags.filter((t) => t.id !== tagId))
}

function createNewTag(): void {
  if (!searchQuery.value.trim()) return
  emit('create-tag', searchQuery.value.trim())
  searchQuery.value = ''
  showDropdown.value = false
}
</script>

<style scoped>
.tag-selector {
  padding: 12px 0;
}
.selected-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.tag-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  border: 1px solid;
}
.tag-remove {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 0;
  margin-left: 2px;
}
.tag-controls {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}
.tag-search {
  flex: 1;
  position: relative;
}
.search-input {
  width: 100%;
  padding: 6px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 13px;
  outline: none;
  box-sizing: border-box;
}
.search-input:focus {
  border-color: var(--accent);
}
.dropdown {
  position: absolute;
  top: 100%;
  left: 0;
  right: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  max-height: 180px;
  overflow-y: auto;
  z-index: 20;
}
.dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text);
}
.dropdown-item:hover, .dropdown-item.selected {
  background: var(--surface-2);
}
.create-item {
  color: var(--accent);
}
.create-icon {
  font-weight: 700;
  font-size: 16px;
}
.tag-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.tag-name { flex: 1; }
.tag-count {
  font-size: 11px;
  color: var(--text-muted);
}
.tag-limit {
  font-size: 12px;
  color: var(--text-muted);
  padding-top: 6px;
  flex-shrink: 0;
}
.tag-limit.exceeded { color: #ef4444; font-weight: 600; }
</style>
