import {useState} from 'react';
import {useNavigate} from 'react-router-dom';
import {Moon, Sun} from 'lucide-react';
import {authService} from '@/services/authService';
import {useChatStore} from '@/stores/useChatStore';
import {useUIStore} from '@/stores/useUIStore';

import {useApps, useCreateApp, useDeleteApp, useTemplates} from '@/hooks/useApps';
import {useAppStore} from '@/stores/useAppStore';

import KnowledgePage from './context/KnowledgePage';
import ToolPage from './context/ToolPage';
import MemoryPage from './context/MemoryPage';
import SkillPage from './context/SkillPage';

type MainTab = 'app' | 'context';
type ContextTab = 'knowledge' | 'tool' | 'memory' | 'skill';

export default function HomePage() {
    const [mainTab, setMainTab] = useState<MainTab>('app');
    const [contextTab, setContextTab] = useState<ContextTab>('knowledge');
    const [showCreateForm, setShowCreateForm] = useState(false);
    const [appCode, setAppCode] = useState('');
    const [templateCode, setTemplateCode] = useState('DEFAULT001');

    const navigate = useNavigate();
    const {data: appsData, isLoading: appsLoading} = useApps();
    const {data: templates} = useTemplates();
    const createAppMutation = useCreateApp();
    const deleteAppMutation = useDeleteApp();
    const {setCurrentApp} = useAppStore();
    const {reset: resetChat} = useChatStore();
    const {toggleTheme, theme} = useUIStore();

    const handleCreateApp = async (e: React.FormEvent) => {
        e.preventDefault();
        try {
            await createAppMutation.mutateAsync({
                app_code: appCode,
                agent_template_code: templateCode,
                enabled: true,
            });
            setShowCreateForm(false);
            setAppCode('');
            alert('App created successfully!');
        } catch (error) {
            alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const handleDeleteApp = async (appId: string, appCode: string) => {
        if (!confirm(`Are you sure you want to delete "${appCode}"?`)) return;
        try {
            await deleteAppMutation.mutateAsync(appId);
            alert('App deleted successfully!');
        } catch (error) {
            alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const handleStartChat = (appId: string, appCode: string) => {
        resetChat();
        setCurrentApp(appId, appCode);
        setTimeout(() => {
            navigate('/chat');
        }, 0);
    };

    const handleLogout = () => {
        authService.logout();
        resetChat();
        navigate('/login');
    };

    const renderContextContent = () => {
        switch (contextTab) {
            case 'knowledge':
                return <KnowledgePage/>;
            case 'tool':
                return <ToolPage/>;
            case 'memory':
                return <MemoryPage/>;
            case 'skill':
                return <SkillPage/>;
            default:
                return <KnowledgePage/>;
        }
    };

    const renderAppContent = () => {
        if (appsLoading) {
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
                        <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100">My Apps</h2>
                        <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
                            Manage your AI applications
                        </p>
                    </div>
                    <button
                        onClick={() => setShowCreateForm(true)}
                        className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
                    >
                        + Create App
                    </button>
                </div>

                {/* Create Form Modal */}
                {showCreateForm && (
                    <div
                        className="fixed inset-0 bg-black bg-opacity-50 dark:bg-opacity-70 flex items-center justify-center z-50">
                        <div
                            className="bg-white dark:bg-navy-800 rounded-lg p-6 max-w-md w-full mx-4 border border-secondary-200 dark:border-navy-700">
                            <h3 className="text-lg font-semibold mb-4 text-navy-900 dark:text-navy-100">
                                Create New App
                            </h3>

                            <form onSubmit={handleCreateApp} className="space-y-4">
                                <div>
                                    <label
                                        className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                                        App Code *
                                    </label>
                                    <input
                                        type="text"
                                        value={appCode}
                                        onChange={(e) => setAppCode(e.target.value)}
                                        placeholder="e.g., my-chat-app"
                                        className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                               bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                               placeholder-secondary-400 dark:placeholder-secondary-500
                               focus:outline-none focus:ring-2 focus:ring-primary-500"
                                        required
                                    />
                                    <p className="text-xs text-secondary-500 dark:text-secondary-400 mt-1">
                                        Unique identifier (letters, numbers, hyphens only)
                                    </p>
                                </div>

                                <div>
                                    <label
                                        className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                                        Agent Template
                                    </label>
                                    <select
                                        value={templateCode}
                                        onChange={(e) => setTemplateCode(e.target.value)}
                                        className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                               bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                               focus:outline-none focus:ring-2 focus:ring-primary-500"
                                    >
                                        <option value="DEFAULT001">Default Template</option>
                                        {templates?.map((template) => (
                                            <option key={template.id} value={template.template_code}>
                                                {template.template_name}
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="flex gap-3 pt-4">
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setShowCreateForm(false);
                                            setAppCode('');
                                        }}
                                        className="flex-1 px-4 py-2 border border-secondary-200 dark:border-navy-600
                               text-secondary-600 dark:text-secondary-300 rounded-md
                               hover:bg-navy-50 dark:hover:bg-navy-700"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        type="submit"
                                        disabled={createAppMutation.isPending}
                                        className="flex-1 px-4 py-2 bg-primary-500 text-white rounded-md
                               hover:bg-primary-600 disabled:opacity-50"
                                    >
                                        {createAppMutation.isPending ? 'Creating...' : 'Create'}
                                    </button>
                                </div>
                            </form>
                        </div>
                    </div>
                )}

                {/* Apps Grid */}
                {appsData?.items && appsData.items.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                        {appsData.items.map((app) => (
                            <div
                                key={app.id}
                                className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700
                           p-6 hover:shadow-lg dark:hover:shadow-navy-900/50 transition-shadow"
                            >
                                <div className="flex items-start justify-between mb-4">
                                    <div className="flex-1">
                                        <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 mb-1">
                                            {app.app_code}
                                        </h3>
                                        <span
                                            className={`inline-block px-2 py-1 text-xs rounded ${
                                                app.enabled
                                                    ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                                                    : 'bg-navy-100 dark:bg-navy-700 text-navy-600 dark:text-navy-400'
                                            }`}
                                        >
                      {app.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                                    </div>
                                </div>

                                <div className="text-sm text-secondary-500 dark:text-secondary-400 mb-4 space-y-1">
                                    <p>ID: {app.id}</p>
                                    <p>Version: v{app.version}</p>
                                    <p className="text-xs">
                                        Created: {new Date(app.created_at).toLocaleDateString()}
                                    </p>
                                </div>

                                <div className="flex gap-2">
                                    <button
                                        onClick={() => handleStartChat(app.id, app.app_code)}
                                        className="flex-1 px-4 py-2 bg-primary-500 text-white text-sm rounded-md hover:bg-primary-600"
                                    >
                                        Start Chat
                                    </button>
                                    <button
                                        onClick={() => handleDeleteApp(app.id, app.app_code)}
                                        disabled={deleteAppMutation.isPending}
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
                                    d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"
                                />
                            </svg>
                        </div>
                        <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                            No Apps Yet
                        </h3>
                        <p className="text-secondary-500 dark:text-secondary-400 mb-4">
                            Create your first app to get started
                        </p>
                        <button
                            onClick={() => setShowCreateForm(true)}
                            className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600"
                        >
                            Create App
                        </button>
                    </div>
                )}

                {/* Pagination */}
                {appsData && appsData.total > appsData.page_size && (
                    <div className="mt-8 flex justify-center">
                        <div className="text-sm text-secondary-500 dark:text-secondary-400">
                            Showing {appsData.items.length} / {appsData.total} Apps
                        </div>
                    </div>
                )}
            </div>
        );
    };

    return (
        <div className="min-h-screen bg-navy-50 dark:bg-navy-950">
            {/* Header */}
            <header className="bg-white dark:bg-navy-900 border-b border-secondary-200 dark:border-navy-700 px-6 py-4">
                <div className="flex items-center justify-between max-w-6xl mx-auto">
                    <h1 className="text-xl font-semibold text-navy-900 dark:text-navy-100">AI Agent Platform</h1>
                    <div className="flex items-center gap-4">
                        <button
                            onClick={toggleTheme}
                            className="p-2 text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100 rounded-lg hover:bg-navy-100 dark:hover:bg-navy-800"
                            title={theme === 'dark' ? '切换到亮色模式' : '切换到暗色模式'}
                        >
                            {theme === 'dark' ? <Sun className="w-5 h-5"/> : <Moon className="w-5 h-5"/>}
                        </button>
                        <button
                            onClick={handleLogout}
                            className="text-sm text-secondary-600 dark:text-secondary-400 hover:text-navy-900 dark:hover:text-navy-100"
                        >
                            Logout
                        </button>
                    </div>
                </div>
            </header>

            <div className="max-w-6xl mx-auto px-6 py-8">
                {/* Main Tabs */}
                <div className="mb-6">
                    <div className="border-b border-secondary-200 dark:border-navy-700">
                        <nav className="-mb-px flex space-x-8">
                            <button
                                onClick={() => setMainTab('app')}
                                className={`py-4 px-1 border-b-2 font-medium text-sm ${
                                    mainTab === 'app'
                                        ? 'border-primary-500 text-primary-500'
                                        : 'border-transparent text-secondary-500 dark:text-secondary-400 hover:text-secondary-700 dark:hover:text-secondary-300 hover:border-secondary-300'
                                }`}
                            >
                                Apps
                            </button>
                            <button
                                onClick={() => setMainTab('context')}
                                className={`py-4 px-1 border-b-2 font-medium text-sm ${
                                    mainTab === 'context'
                                        ? 'border-primary-500 text-primary-500'
                                        : 'border-transparent text-secondary-500 dark:text-secondary-400 hover:text-secondary-700 dark:hover:text-secondary-300 hover:border-secondary-300'
                                }`}
                            >
                                Context
                            </button>
                        </nav>
                    </div>
                </div>

                {/* Context Sub-tabs */}
                {mainTab === 'context' && (
                    <div className="mb-6">
                        <div className="flex space-x-4">
                            {(['knowledge', 'tool', 'memory', 'skill'] as ContextTab[]).map((tab) => (
                                <button
                                    key={tab}
                                    onClick={() => setContextTab(tab)}
                                    className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                                        contextTab === tab
                                            ? 'bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-400'
                                            : 'text-secondary-600 dark:text-secondary-400 hover:bg-navy-100 dark:hover:bg-navy-800'
                                    }`}
                                >
                                    {tab.charAt(0).toUpperCase() + tab.slice(1)}
                                </button>
                            ))}
                        </div>
                    </div>
                )}

                {/* Content */}
                {mainTab === 'app' ? renderAppContent() : renderContextContent()}
            </div>
        </div>
    );
}
