import {useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {useKnowledgeList, useCreateKnowledge, useDeleteKnowledge} from '@/hooks/useKnowledge';
import {generateKnowledgeDocumentsRoute} from '@/constants/routes';
import type {KnowledgeCreate} from '@/types/knowledge';

export default function KnowledgePage() {
    const navigate = useNavigate();
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [formData, setFormData] = useState<KnowledgeCreate>({
        name: '',
        description: '',
        provider: 'default',
        indexing_technique: 'high_quality',
        permission: 'private',
    });

    const {data: knowledgeData, isLoading} = useKnowledgeList();
    const createMutation = useCreateKnowledge();
    const deleteMutation = useDeleteKnowledge();

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            await createMutation.mutateAsync(formData);
            setShowCreateForm(false);
            setFormData({
                name: '',
                description: '',
                provider: 'default',
                indexing_technique: 'high_quality',
                permission: 'private',
            });
            alert('Knowledge base created successfully!');
        } catch (error) {
            alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const handleDelete = async (id: string, name: string) => {
        if (!confirm(`Are you sure you want to delete "${name}"?`)) return;
        try {
            await deleteMutation.mutateAsync(id);
            alert('Knowledge base deleted successfully!');
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
                    <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100">Knowledge Bases</h2>
                    <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
                        Manage your knowledge bases for AI context
                    </p>
                </div>
                <button
                    onClick={() => setShowCreateForm(true)}
                    className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
                >
                    + Create Knowledge
                </button>
            </div>

            {/* Create Form Modal */}
            {showCreateForm && (
                <div
                    className="fixed inset-0 bg-black bg-opacity-50 dark:bg-opacity-70 flex items-center justify-center z-50">
                    <div
                        className="bg-white dark:bg-navy-800 rounded-lg p-6 max-w-md w-full mx-4 border border-secondary-200 dark:border-navy-700">
                        <h3 className="text-lg font-semibold mb-4 text-navy-900 dark:text-navy-100">
                            Create Knowledge Base
                        </h3>

                        <form onSubmit={handleCreate} className="space-y-4">
                            <div>
                                <label
                                    className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                                    Name *
                                </label>
                                <input
                                    type="text"
                                    value={formData.name}
                                    onChange={(e) => setFormData({...formData, name: e.target.value})}
                                    placeholder="e.g., Product Documentation"
                                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                             bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                             placeholder-secondary-400 dark:placeholder-secondary-500
                             focus:outline-none focus:ring-2 focus:ring-primary-500"
                                    required
                                />
                            </div>

                            <div>
                                <label
                                    className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                                    Description
                                </label>
                                <textarea
                                    value={formData.description || ''}
                                    onChange={(e) => setFormData({...formData, description: e.target.value})}
                                    placeholder="Describe the purpose of this knowledge base"
                                    rows={3}
                                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                             bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                             placeholder-secondary-400 dark:placeholder-secondary-500
                             focus:outline-none focus:ring-2 focus:ring-primary-500"
                                />
                            </div>

                            <div>
                                <label
                                    className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                                    Indexing Technique
                                </label>
                                <select
                                    value={formData.indexing_technique}
                                    onChange={(e) => setFormData({...formData, indexing_technique: e.target.value})}
                                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                             bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                             focus:outline-none focus:ring-2 focus:ring-primary-500"
                                >
                                    <option value="high_quality">High Quality</option>
                                    <option value="economy">Economy</option>
                                </select>
                            </div>

                            <div>
                                <label
                                    className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                                    Permission
                                </label>
                                <select
                                    value={formData.permission}
                                    onChange={(e) => setFormData({...formData, permission: e.target.value})}
                                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                             bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                             focus:outline-none focus:ring-2 focus:ring-primary-500"
                                >
                                    <option value="private">Private</option>
                                    <option value="public">Public</option>
                                </select>
                            </div>

                            <div className="flex gap-3 pt-4">
                                <button
                                    type="button"
                                    onClick={() => {
                                        setShowCreateForm(false);
                                        setFormData({
                                            name: '',
                                            description: '',
                                            provider: 'default',
                                            indexing_technique: 'high_quality',
                                            permission: 'private',
                                        });
                                    }}
                                    className="flex-1 px-4 py-2 border border-secondary-200 dark:border-navy-600
                             text-secondary-600 dark:text-secondary-300 rounded-md
                             hover:bg-navy-50 dark:hover:bg-navy-700"
                                >
                                    Cancel
                                </button>
                                <button
                                    type="submit"
                                    disabled={createMutation.isPending}
                                    className="flex-1 px-4 py-2 bg-primary-500 text-white rounded-md
                             hover:bg-primary-600 disabled:opacity-50"
                                >
                                    {createMutation.isPending ? 'Creating...' : 'Create'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Knowledge Grid */}
            {knowledgeData?.items && knowledgeData.items.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                    {knowledgeData.items.map((knowledge) => (
                        <div
                            key={knowledge.id}
                            className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700
                         p-6 hover:shadow-lg dark:hover:shadow-navy-900/50 transition-shadow"
                        >
                            <div className="flex items-start justify-between mb-4">
                                <div className="flex-1">
                                    <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 mb-1">
                                        {knowledge.name}
                                    </h3>
                                    <div className="flex gap-2">
                    <span
                        className={`inline-block px-2 py-1 text-xs rounded ${
                            knowledge.status === 'active'
                                ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                                : 'bg-navy-100 dark:bg-navy-700 text-navy-600 dark:text-navy-400'
                        }`}
                    >
                      {knowledge.status}
                    </span>
                                        <span
                                            className="inline-block px-2 py-1 text-xs rounded bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400">
                      {knowledge.permission}
                    </span>
                                    </div>
                                </div>
                            </div>

                            {knowledge.description && (
                                <p className="text-sm text-secondary-600 dark:text-secondary-400 mb-4 line-clamp-2">
                                    {knowledge.description}
                                </p>
                            )}

                            <div className="text-sm text-secondary-500 dark:text-secondary-400 mb-4 space-y-1">
                                <p>Documents: {knowledge.document_count}</p>
                                <p>Chunks: {knowledge.chunk_count}</p>
                                <p>Technique: {knowledge.indexing_technique}</p>
                                <p className="text-xs">
                                    Created: {new Date(knowledge.created_at).toLocaleDateString()}
                                </p>
                            </div>

                            <div className="flex gap-2">
                                <button
                                    onClick={() => navigate(generateKnowledgeDocumentsRoute(knowledge.id))}
                                    className="flex-1 px-4 py-2 bg-primary-500 text-white text-sm rounded-md hover:bg-primary-600"
                                >
                                    Manage
                                </button>
                                <button
                                    onClick={() => handleDelete(knowledge.id, knowledge.name)}
                                    disabled={deleteMutation.isPending}
                                    className="px-4 py-2 border border-red-300 dark:border-red-700
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
                                d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
                            />
                        </svg>
                    </div>
                    <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                        No Knowledge Bases
                    </h3>
                    <p className="text-secondary-500 dark:text-secondary-400 mb-4">
                        Create your first knowledge base to get started
                    </p>
                    <button
                        onClick={() => setShowCreateForm(true)}
                        className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600"
                    >
                        Create Knowledge Base
                    </button>
                </div>
            )}

            {/* Pagination */}
            {knowledgeData && knowledgeData.total > knowledgeData.page_size && (
                <div className="mt-8 flex justify-center">
                    <div className="text-sm text-secondary-500 dark:text-secondary-400">
                        Showing {knowledgeData.items.length} / {knowledgeData.total} knowledge bases
                    </div>
                </div>
            )}
        </div>
    );
}
