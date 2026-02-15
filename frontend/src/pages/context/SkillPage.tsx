import { useState } from 'react';
import {
  useSkills,
  useCreateSkill,
  useUpdateSkill,
  useDeleteSkill,
} from '@/hooks/useSkills';
import type { SkillCreate, Skill } from '@/types/skill';

const INITIAL_FORM: SkillCreate = {
  name: '',
  description: '',
  content: '',
  tags: [],
};

export default function SkillPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formData, setFormData] = useState<SkillCreate>(INITIAL_FORM);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');

  const { data: skillData, isLoading } = useSkills({
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const createMutation = useCreateSkill();
  const updateMutation = useUpdateSkill();
  const deleteMutation = useDeleteSkill();

  const skills = skillData?.items ?? [];

  // Extract all unique tags from skills
  const allTags = Array.from(
    new Set(skills.flatMap((skill) => skill.tags ?? []))
  ).sort();

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const clearTags = () => setSelectedTags([]);

  const addTagToForm = () => {
    if (tagInput.trim() && !formData.tags?.includes(tagInput.trim())) {
      setFormData({
        ...formData,
        tags: [...(formData.tags || []), tagInput.trim()],
      });
      setTagInput('');
    }
  };

  const removeTagFromForm = (tag: string) => {
    setFormData({
      ...formData,
      tags: formData.tags?.filter((t) => t !== tag) || [],
    });
  };

  const openCreateModal = () => {
    setFormData(INITIAL_FORM);
    setShowCreateModal(true);
  };

  const openEditModal = (skill: Skill) => {
    setEditingSkill(skill);
    setFormData({
      name: skill.name,
      description: skill.description || '',
      content: skill.content,
      tags: skill.tags || [],
    });
    setShowEditModal(true);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync(formData);
      setShowCreateModal(false);
      setFormData(INITIAL_FORM);
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSkill) return;

    try {
      await updateMutation.mutateAsync({
        id: editingSkill.id,
        data: formData,
      });
      setShowEditModal(false);
      setEditingSkill(null);
      setFormData(INITIAL_FORM);
    } catch (error) {
      alert(`Update failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDelete = async (skill: Skill) => {
    if (!confirm(`Are you sure you want to delete "${skill.name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(skill.id);
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-secondary-500 dark:text-secondary-400">Loading...</div>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100">Skills</h2>
          <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
            Manage reusable skill sets in Markdown format
          </p>
        </div>
        <button
          onClick={openCreateModal}
          className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          + Create Skill
        </button>
      </div>

      {/* Tag Filter */}
      {allTags.length > 0 && (
        <div className="mb-6 bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-navy-900 dark:text-navy-100">
              Filter by Tags
            </h3>
            {selectedTags.length > 0 && (
              <button
                onClick={clearTags}
                className="text-xs text-primary-500 hover:text-primary-600 font-medium"
              >
                Clear All
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => {
              const isSelected = selectedTags.includes(tag);
              return (
                <button
                  key={tag}
                  onClick={() => toggleTag(tag)}
                  className={`px-3 py-1.5 text-sm rounded-full font-medium transition-colors ${
                    isSelected
                      ? 'bg-primary-500 text-white hover:bg-primary-600'
                      : 'bg-secondary-100 dark:bg-navy-700 text-secondary-700 dark:text-secondary-300 hover:bg-secondary-200 dark:hover:bg-navy-600'
                  }`}
                >
                  {tag}
                  {isSelected && ' ✓'}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Create/Edit Modal */}
      {(showCreateModal || showEditModal) && (
        <div className="fixed inset-0 bg-black bg-opacity-50 dark:bg-opacity-70 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-navy-800 rounded-lg p-6 max-w-4xl w-full mx-4 border border-secondary-200 dark:border-navy-700 max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold mb-4 text-navy-900 dark:text-navy-100">
              {showCreateModal ? 'Create Skill' : 'Edit Skill'}
            </h3>

            <form onSubmit={showCreateModal ? handleCreate : handleUpdate} className="space-y-4">
              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Name *
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="e.g., Python Best Practices"
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                    placeholder-secondary-400 dark:placeholder-secondary-500
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                  required
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Description
                </label>
                <input
                  type="text"
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="Brief description of this skill"
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                    placeholder-secondary-400 dark:placeholder-secondary-500
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                />
              </div>

              {/* Tags */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Tags
                </label>
                <div className="flex gap-2 mb-2">
                  <input
                    type="text"
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTagToForm())}
                    placeholder="Add a tag..."
                    className="flex-1 px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                      bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                      placeholder-secondary-400 dark:placeholder-secondary-500
                      focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                  <button
                    type="button"
                    onClick={addTagToForm}
                    className="px-4 py-2 bg-secondary-200 dark:bg-navy-700 text-secondary-700 dark:text-secondary-300 rounded-md hover:bg-secondary-300 dark:hover:bg-navy-600"
                  >
                    Add
                  </button>
                </div>
                <div className="flex flex-wrap gap-2">
                  {formData.tags?.map((tag) => (
                    <span
                      key={tag}
                      className="px-3 py-1 text-sm rounded-full bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 flex items-center gap-2"
                    >
                      {tag}
                      <button
                        type="button"
                        onClick={() => removeTagFromForm(tag)}
                        className="hover:text-primary-900 dark:hover:text-primary-100"
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>

              {/* Content (Markdown) */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Content (Markdown) *
                </label>
                <textarea
                  value={formData.content}
                  onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                  rows={16}
                  placeholder="# Skill Title&#10;&#10;## Description&#10;Write your skill documentation in Markdown...&#10;&#10;```python&#10;# Code examples&#10;```"
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100 font-mono text-sm
                    placeholder-secondary-400 dark:placeholder-secondary-500
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                  required
                />
                <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                  Supports Markdown formatting with code blocks, headers, lists, etc.
                </p>
              </div>

              {/* Actions */}
              <div className="flex gap-3 pt-4">
                <button
                  type="button"
                  onClick={() => {
                    setShowCreateModal(false);
                    setShowEditModal(false);
                    setFormData(INITIAL_FORM);
                    setEditingSkill(null);
                  }}
                  className="flex-1 px-4 py-2 border border-secondary-200 dark:border-navy-600
                    text-secondary-600 dark:text-secondary-300 rounded-md
                    hover:bg-navy-50 dark:hover:bg-navy-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={createMutation.isPending || updateMutation.isPending}
                  className="flex-1 px-4 py-2 bg-primary-500 text-white rounded-md
                    hover:bg-primary-600 disabled:opacity-50"
                >
                  {createMutation.isPending || updateMutation.isPending
                    ? 'Saving...'
                    : showCreateModal
                    ? 'Create Skill'
                    : 'Update Skill'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Skills Grid */}
      {skills.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {skills.map((skill) => (
            <div
              key={skill.id}
              className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700
                p-6 hover:shadow-lg dark:hover:shadow-navy-900/50 transition-shadow"
            >
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1 min-w-0">
                  <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 truncate">
                    {skill.name}
                  </h3>
                  {skill.description && (
                    <p className="text-xs text-secondary-400 dark:text-secondary-500 mt-1">
                      {skill.description}
                    </p>
                  )}
                </div>
                {skill.has_embedding && (
                  <span className="inline-block px-2 py-0.5 text-xs rounded font-medium bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 ml-2 flex-shrink-0">
                    Indexed
                  </span>
                )}
              </div>

              {/* Tags */}
              {skill.tags && skill.tags.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-3">
                  {skill.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-2 py-0.5 text-xs rounded-full bg-secondary-100 dark:bg-navy-700 text-secondary-600 dark:text-secondary-300"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}

              {/* Content preview */}
              <div className="text-sm text-secondary-600 dark:text-secondary-400 mb-4 line-clamp-3 font-mono">
                {skill.content.substring(0, 150)}...
              </div>

              {/* Meta */}
              <div className="text-xs text-secondary-500 dark:text-secondary-400 mb-4">
                <p>Created: {new Date(skill.created_at).toLocaleDateString()}</p>
                {skill.summary && (
                  <p className="mt-1 line-clamp-2">{skill.summary}</p>
                )}
              </div>

              {/* Actions */}
              <div className="flex gap-2">
                <button
                  onClick={() => openEditModal(skill)}
                  className="flex-1 px-3 py-1.5 text-sm rounded-md border border-secondary-200 dark:border-navy-600
                    text-secondary-600 dark:text-secondary-300 hover:bg-navy-50 dark:hover:bg-navy-700"
                >
                  Edit
                </button>
                <button
                  onClick={() => handleDelete(skill)}
                  disabled={deleteMutation.isPending}
                  className="px-3 py-1.5 border border-red-300 dark:border-red-700
                    text-red-600 dark:text-red-400 text-sm rounded-md
                    hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        /* Empty State */
        <div className="text-center py-12">
          <div className="text-secondary-400 dark:text-secondary-600 mb-4">
            <svg
              className="mx-auto h-12 w-12"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 10V3L4 14h7v7l9-11h-7z"
              />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
            No Skills Yet
          </h3>
          <p className="text-secondary-500 dark:text-secondary-400 mb-4">
            Create your first skill to build reusable knowledge in Markdown format
          </p>
          <button
            onClick={openCreateModal}
            className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600"
          >
            Create Skill
          </button>
        </div>
      )}
    </div>
  );
}
