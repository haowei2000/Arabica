import {useState, useRef, useCallback} from 'react';
import {useParams, useNavigate} from 'react-router-dom';
import {useKnowledge} from '@/hooks/useKnowledge';
import {useDocumentList, useUploadDocument, useDeleteDocument} from '@/hooks/useDocuments';
import type {Document} from '@/types/document';

type UploadingFile = {
    file: File;
    taskId: string | null;
    status: 'uploading' | 'processing' | 'success' | 'error' | 'duplicate';
    error?: string;
};

export default function DocumentPage() {
    const {knowledgeId} = useParams<{ knowledgeId: string }>();
    const navigate = useNavigate();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
    const [dragOver, setDragOver] = useState(false);

    // Fetch knowledge details
    const {data: knowledge, isLoading: knowledgeLoading} = useKnowledge(knowledgeId || '');

    // Fetch documents
    const {data: documentsData, isLoading: documentsLoading} = useDocumentList(knowledgeId || '');

    // Mutations
    const uploadMutation = useUploadDocument();
    const deleteMutation = useDeleteDocument(knowledgeId || '');

    const handleFileUpload = useCallback(async (files: FileList | null) => {
        if (!files || !knowledgeId) return;

        const fileArray = Array.from(files);

        for (const file of fileArray) {
            // Add to uploading list
            const uploadingFile: UploadingFile = {
                file,
                taskId: null,
                status: 'uploading',
            };
            setUploadingFiles(prev => [...prev, uploadingFile]);

            try {
                const result = await uploadMutation.mutateAsync({
                    file,
                    knowledge_id: knowledgeId,
                });

                // Update status based on result
                setUploadingFiles(prev =>
                    prev.map(f =>
                        f.file === file
                            ? {
                                ...f,
                                taskId: result.task_id,
                                status: result.task_id === 'duplicate' ? 'duplicate' : 'success',
                            }
                            : f
                    )
                );

                // Remove from uploading list after delay
                setTimeout(() => {
                    setUploadingFiles(prev => prev.filter(f => f.file !== file));
                }, 3000);
            } catch (error) {
                setUploadingFiles(prev =>
                    prev.map(f =>
                        f.file === file
                            ? {
                                ...f,
                                status: 'error',
                                error: error instanceof Error ? error.message : 'Upload failed',
                            }
                            : f
                    )
                );
            }
        }
    }, [knowledgeId, uploadMutation]);

    const handleDragOver = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(true);
    }, []);

    const handleDragLeave = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
    }, []);

    const handleDrop = useCallback((e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
        handleFileUpload(e.dataTransfer.files);
    }, [handleFileUpload]);

    const handleDelete = async (document: Document) => {
        if (!confirm(`Are you sure you want to delete "${document.original_name}"?`)) return;
        try {
            await deleteMutation.mutateAsync(document.id);
        } catch (error) {
            alert(`Delete failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const formatFileSize = (bytes: number): string => {
        if (bytes === 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    };

    const getFileIcon = (mimeType?: string): string => {
        if (!mimeType) return 'file';
        if (mimeType.includes('pdf')) return 'pdf';
        if (mimeType.includes('word') || mimeType.includes('document')) return 'doc';
        if (mimeType.includes('text')) return 'txt';
        if (mimeType.includes('image')) return 'image';
        return 'file';
    };

    if (knowledgeLoading) {
        return (
            <div className="min-h-screen bg-navy-50 dark:bg-navy-950 flex items-center justify-center">
                <div className="text-secondary-500 dark:text-secondary-400">Loading...</div>
            </div>
        );
    }

    if (!knowledge) {
        return (
            <div className="min-h-screen bg-navy-50 dark:bg-navy-950 flex items-center justify-center">
                <div className="text-center">
                    <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100 mb-2">
                        Knowledge Base Not Found
                    </h2>
                    <button
                        onClick={() => navigate('/home')}
                        className="text-primary-500 hover:text-primary-600"
                    >
                        Go back to Home
                    </button>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-navy-50 dark:bg-navy-950">
            {/* Header */}
            <div className="bg-white dark:bg-navy-900 border-b border-secondary-200 dark:border-navy-700">
                <div className="max-w-6xl mx-auto px-6 py-4">
                    <div className="flex items-center gap-4">
                        <button
                            onClick={() => navigate('/home')}
                            className="text-secondary-500 hover:text-secondary-700 dark:text-secondary-400 dark:hover:text-secondary-200"
                        >
                            <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7"/>
                            </svg>
                        </button>
                        <div>
                            <h1 className="text-xl font-bold text-navy-900 dark:text-navy-100">
                                {knowledge.name}
                            </h1>
                            <p className="text-sm text-secondary-500 dark:text-secondary-400">
                                {knowledge.document_count} documents · {knowledge.chunk_count} chunks
                            </p>
                        </div>
                    </div>
                </div>
            </div>

            <div className="max-w-6xl mx-auto px-6 py-8">
                {/* Upload Area */}
                <div
                    className={`border-2 border-dashed rounded-lg p-8 mb-8 text-center transition-colors
            ${dragOver
                        ? 'border-primary-500 bg-primary-50 dark:bg-primary-900/20'
                        : 'border-secondary-300 dark:border-navy-600 hover:border-primary-400 dark:hover:border-primary-500'
                    }`}
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                >
                    <input
                        ref={fileInputRef}
                        type="file"
                        className="hidden"
                        multiple
                        accept=".pdf,.doc,.docx,.txt,.md,.html,.csv,.json"
                        onChange={(e) => handleFileUpload(e.target.files)}
                    />
                    <div className="text-secondary-400 dark:text-secondary-500 mb-4">
                        <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={1.5}
                                d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                            />
                        </svg>
                    </div>
                    <p className="text-navy-700 dark:text-navy-300 mb-2">
                        Drag and drop files here, or{' '}
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="text-primary-500 hover:text-primary-600 font-medium"
                        >
                            browse
                        </button>
                    </p>
                    <p className="text-sm text-secondary-500 dark:text-secondary-400">
                        Supported: PDF, DOC, DOCX, TXT, MD, HTML, CSV, JSON
                    </p>
                </div>

                {/* Uploading Files */}
                {uploadingFiles.length > 0 && (
                    <div className="mb-8 space-y-2">
                        <h3 className="text-sm font-medium text-navy-700 dark:text-navy-300 mb-2">
                            Uploading
                        </h3>
                        {uploadingFiles.map((uploadingFile, index) => (
                            <div
                                key={index}
                                className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 p-4 flex items-center gap-4"
                            >
                                <div className="flex-1">
                                    <p className="text-sm font-medium text-navy-900 dark:text-navy-100">
                                        {uploadingFile.file.name}
                                    </p>
                                    <p className="text-xs text-secondary-500 dark:text-secondary-400">
                                        {formatFileSize(uploadingFile.file.size)}
                                    </p>
                                </div>
                                <div>
                                    {uploadingFile.status === 'uploading' && (
                                        <span className="inline-flex items-center gap-2 text-sm text-primary-500">
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"
                                fill="none"/>
                        <path className="opacity-75" fill="currentColor"
                              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                      </svg>
                      Uploading...
                    </span>
                                    )}
                                    {uploadingFile.status === 'processing' && (
                                        <span className="inline-flex items-center gap-2 text-sm text-yellow-500">
                      <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"
                                fill="none"/>
                        <path className="opacity-75" fill="currentColor"
                              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                      </svg>
                      Processing...
                    </span>
                                    )}
                                    {uploadingFile.status === 'success' && (
                                        <span className="inline-flex items-center gap-2 text-sm text-green-500">
                      <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd"
                              d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z"
                              clipRule="evenodd"/>
                      </svg>
                      Uploaded
                    </span>
                                    )}
                                    {uploadingFile.status === 'duplicate' && (
                                        <span className="inline-flex items-center gap-2 text-sm text-yellow-500">
                      <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd"
                              d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z"
                              clipRule="evenodd"/>
                      </svg>
                      Duplicate
                    </span>
                                    )}
                                    {uploadingFile.status === 'error' && (
                                        <span className="inline-flex items-center gap-2 text-sm text-red-500">
                      <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd"
                              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                              clipRule="evenodd"/>
                      </svg>
                                            {uploadingFile.error || 'Error'}
                    </span>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                )}

                {/* Documents List */}
                <div>
                    <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 mb-4">
                        Documents
                    </h3>

                    {documentsLoading ? (
                        <div className="text-center py-8">
                            <div className="text-secondary-500 dark:text-secondary-400">Loading documents...</div>
                        </div>
                    ) : documentsData?.items && documentsData.items.length > 0 ? (
                        <div
                            className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 overflow-hidden">
                            <table className="w-full">
                                <thead className="bg-navy-50 dark:bg-navy-900">
                                <tr>
                                    <th className="text-left px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                        Name
                                    </th>
                                    <th className="text-left px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                        Status
                                    </th>
                                    <th className="text-left px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                        Chunks
                                    </th>
                                    <th className="text-left px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                        Size
                                    </th>
                                    <th className="text-left px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                        Uploaded
                                    </th>
                                    <th className="text-right px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                        Actions
                                    </th>
                                </tr>
                                </thead>
                                <tbody className="divide-y divide-secondary-200 dark:divide-navy-700">
                                {documentsData.items.map((document) => (
                                    <tr key={document.id} className="hover:bg-navy-50 dark:hover:bg-navy-700/50">
                                        <td className="px-6 py-4">
                                            <div className="flex items-center gap-3">
                                                <div
                                                    className="flex-shrink-0 w-8 h-8 bg-primary-100 dark:bg-primary-900/30 rounded flex items-center justify-center">
                            <span className="text-xs font-medium text-primary-600 dark:text-primary-400 uppercase">
                              {getFileIcon(document.mime_type).slice(0, 3)}
                            </span>
                                                </div>
                                                <div className="flex flex-col">
                            <span className="text-sm font-medium text-navy-900 dark:text-navy-100 truncate max-w-xs">
                              {document.original_name}
                            </span>
                                                    <span
                                                        className="text-xs text-secondary-500 dark:text-secondary-400">
                              {document.mime_type?.split('/').pop() || 'Unknown'}
                            </span>
                                                </div>
                                            </div>
                                        </td>
                                        <td className="px-6 py-4">
                        <span
                            className={`inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium rounded-full ${
                                document.status === 'completed'
                                    ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                                    : document.status === 'processing'
                                        ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
                                        : document.status === 'failed'
                                            ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
                                            : 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
                            }`}
                        >
                          {document.status === 'processing' && (
                              <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24">
                                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor"
                                          strokeWidth="4" fill="none"/>
                                  <path className="opacity-75" fill="currentColor"
                                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                              </svg>
                          )}
                            {document.status}
                        </span>
                                            {document.error_message && (
                                                <p className="text-xs text-red-500 mt-1 max-w-xs truncate"
                                                   title={document.error_message}>
                                                    {document.error_message}
                                                </p>
                                            )}
                                        </td>
                                        <td className="px-6 py-4">
                        <span className="text-sm text-secondary-600 dark:text-secondary-400">
                          {document.chunk_count}
                        </span>
                                        </td>
                                        <td className="px-6 py-4">
                        <span className="text-sm text-secondary-600 dark:text-secondary-400">
                          {formatFileSize(document.file_size)}
                        </span>
                                        </td>
                                        <td className="px-6 py-4">
                        <span className="text-sm text-secondary-600 dark:text-secondary-400">
                          {new Date(document.created_at).toLocaleDateString()}
                        </span>
                                        </td>
                                        <td className="px-6 py-4 text-right">
                                            <button
                                                onClick={() => handleDelete(document)}
                                                disabled={deleteMutation.isPending}
                                                className="text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300 text-sm font-medium disabled:opacity-50"
                                            >
                                                Delete
                                            </button>
                                        </td>
                                    </tr>
                                ))}
                                </tbody>
                            </table>
                        </div>
                    ) : (
                        <div
                            className="text-center py-12 bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700">
                            <div className="text-secondary-400 dark:text-secondary-500 mb-4">
                                <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24"
                                     stroke="currentColor">
                                    <path
                                        strokeLinecap="round"
                                        strokeLinejoin="round"
                                        strokeWidth={1.5}
                                        d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                                    />
                                </svg>
                            </div>
                            <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                                No Documents Yet
                            </h3>
                            <p className="text-secondary-500 dark:text-secondary-400">
                                Upload your first document to get started
                            </p>
                        </div>
                    )}

                    {/* Pagination */}
                    {documentsData && documentsData.total > documentsData.page_size && (
                        <div className="mt-4 flex justify-center">
                            <div className="text-sm text-secondary-500 dark:text-secondary-400">
                                Showing {documentsData.items.length} of {documentsData.total} documents
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
