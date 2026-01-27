import {useCallback, useRef, useState} from 'react';
import {useNavigate, useParams} from 'react-router-dom';
import {useQuery} from '@tanstack/react-query';
import {useKnowledge} from '@/hooks/useKnowledge';
import {useDeleteDocument, useDocumentList, useUploadDocument} from '@/hooks/useDocuments';
import {useChunksByDocument} from '@/hooks/useChunks';
import {documentService} from '@/services/documentService';
import {API_BASE_URL, API_ENDPOINTS} from '@/constants/api';
import type {Document} from '@/types/document';
import type {Chunk} from '@/types/chunk';

type UploadingFile = {
    file: File;
    taskId: string | null;
    status: 'uploading' | 'processing' | 'success' | 'error' | 'duplicate';
    error?: string;
};

type ViewMode = 'documents' | 'chunks' | 'preview';

function ChunkCard({chunk, index}: { chunk: Chunk; index: number }) {
    const [expanded, setExpanded] = useState(false);
    const contentPreview = chunk.content.length > 200
        ? chunk.content.slice(0, 200) + '...'
        : chunk.content;

    const position = chunk.meta?.position !== undefined ? chunk.meta.position : index;

    return (
        <div className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 p-4">
            <div className="flex items-start justify-between gap-4 mb-2">
                <div className="flex items-center gap-2 text-xs text-secondary-500 dark:text-secondary-400">
                    <span
                        className="bg-primary-100 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300 px-2 py-0.5 rounded">
                        #{position + 1}
                    </span>
                    {chunk.meta?.content_length && (
                        <span>{chunk.meta.content_length} chars</span>
                    )}
                </div>
                <button
                    onClick={() => setExpanded(!expanded)}
                    className="text-xs text-primary-500 hover:text-primary-600 dark:text-primary-400 dark:hover:text-primary-300 whitespace-nowrap"
                >
                    {expanded ? 'Collapse' : 'Expand'}
                </button>
            </div>
            <div className="text-sm text-navy-700 dark:text-navy-200 whitespace-pre-wrap break-words">
                {expanded ? chunk.content : contentPreview}
            </div>
            {chunk.keywords && chunk.keywords.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1">
                    {chunk.keywords.slice(0, expanded ? undefined : 5).map((keyword, idx) => (
                        <span
                            key={idx}
                            className="text-xs bg-secondary-100 dark:bg-navy-700 text-secondary-600 dark:text-secondary-300 px-2 py-0.5 rounded"
                        >
                            {keyword}
                        </span>
                    ))}
                    {!expanded && chunk.keywords.length > 5 && (
                        <span className="text-xs text-secondary-500">+{chunk.keywords.length - 5} more</span>
                    )}
                </div>
            )}
        </div>
    );
}

function ChunksView({
                        document,
                        onBack
                    }: {
    document: Document;
    onBack: () => void;
}) {
    const [page, setPage] = useState(1);
    const pageSize = 20;

    const {data: chunksData, isLoading} = useChunksByDocument(
        document.id,
        {page, page_size: pageSize}
    );

    const totalPages = chunksData ? Math.ceil(chunksData.total / pageSize) : 0;

    return (
        <div>
            {/* Chunks Header */}
            <div className="flex items-center gap-4 mb-6">
                <button
                    onClick={onBack}
                    className="text-secondary-500 hover:text-secondary-700 dark:text-secondary-400 dark:hover:text-secondary-200"
                >
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7"/>
                    </svg>
                </button>
                <div>
                    <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100">
                        {document.original_name}
                    </h3>
                    <p className="text-sm text-secondary-500 dark:text-secondary-400">
                        {chunksData?.total ?? document.chunk_count} chunks
                    </p>
                </div>
            </div>

            {/* Chunks List */}
            {isLoading ? (
                <div className="text-center py-8">
                    <div className="text-secondary-500 dark:text-secondary-400">Loading chunks...</div>
                </div>
            ) : chunksData?.items && chunksData.items.length > 0 ? (
                <div className="space-y-4">
                    {chunksData.items.map((chunk, index) => (
                        <ChunkCard
                            key={chunk.id}
                            chunk={chunk}
                            index={(page - 1) * pageSize + index}
                        />
                    ))}
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
                                d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4"
                            />
                        </svg>
                    </div>
                    <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                        No Chunks Yet
                    </h3>
                    <p className="text-secondary-500 dark:text-secondary-400">
                        This document has no chunks
                    </p>
                </div>
            )}

            {/* Pagination */}
            {chunksData && totalPages > 1 && (
                <div className="mt-6 flex items-center justify-between">
                    <div className="text-sm text-secondary-500 dark:text-secondary-400">
                        Showing {((page - 1) * pageSize) + 1} - {Math.min(page * pageSize, chunksData.total)} of {chunksData.total} chunks
                    </div>
                    <div className="flex items-center gap-2">
                        <button
                            onClick={() => setPage(p => Math.max(1, p - 1))}
                            disabled={page === 1}
                            className="px-3 py-1.5 text-sm font-medium rounded-lg border border-secondary-300 dark:border-navy-600 text-secondary-700 dark:text-secondary-300 hover:bg-secondary-50 dark:hover:bg-navy-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Previous
                        </button>
                        <span className="text-sm text-secondary-600 dark:text-secondary-400">
                            Page {page} of {totalPages}
                        </span>
                        <button
                            onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                            disabled={page >= totalPages}
                            className="px-3 py-1.5 text-sm font-medium rounded-lg border border-secondary-300 dark:border-navy-600 text-secondary-700 dark:text-secondary-300 hover:bg-secondary-50 dark:hover:bg-navy-700 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                            Next
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
}

function PreviewView({
                         document,
                         onBack
                     }: {
    document: Document;
    onBack: () => void;
}) {
    const {data: previewData, isLoading, error} = useQuery({
        queryKey: ['document', 'preview', document.id],
        queryFn: () => documentService.getPreview(document.id),
    });

    return (
        <div>
            {/* Preview Header */}
            <div className="flex items-center justify-between gap-4 mb-6">
                <div className="flex items-center gap-4">
                    <button
                        onClick={onBack}
                        className="text-secondary-500 hover:text-secondary-700 dark:text-secondary-400 dark:hover:text-secondary-200"
                    >
                        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7"/>
                        </svg>
                    </button>
                    <div>
                        <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100">
                            {document.original_name}
                        </h3>
                        <p className="text-sm text-secondary-500 dark:text-secondary-400">
                            {previewData?.content_length ?? 0} characters
                        </p>
                    </div>
                </div>
            </div>

            {/* Preview Content */}
            {isLoading ? (
                <div className="text-center py-8">
                    <div className="text-secondary-500 dark:text-secondary-400">Loading preview...</div>
                </div>
            ) : error ? (
                <div
                    className="text-center py-12 bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700">
                    <div className="text-red-500 mb-2">Failed to load preview</div>
                    <p className="text-sm text-secondary-500">{error instanceof Error ? error.message : 'Unknown error'}</p>
                </div>
            ) : previewData?.content ? (
                <div
                    className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 p-6">
                    <div className="prose dark:prose-invert max-w-none">
                        <pre className="whitespace-pre-wrap text-sm text-navy-700 dark:text-navy-200 font-sans">
                            {previewData.content}
                        </pre>
                    </div>
                </div>
            ) : (
                <div
                    className="text-center py-12 bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700">
                    <div className="text-secondary-400 dark:text-secondary-500 mb-4">
                        <svg className="mx-auto h-12 w-12" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                        </svg>
                    </div>
                    <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
                        No Preview Available
                    </h3>
                    <p className="text-secondary-500 dark:text-secondary-400">
                        This document has no preview content
                    </p>
                </div>
            )}
        </div>
    );
}

export default function DocumentPage() {
    const {knowledgeId} = useParams<{ knowledgeId: string }>();
    const navigate = useNavigate();
    const fileInputRef = useRef<HTMLInputElement>(null);

    const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
    const [dragOver, setDragOver] = useState(false);
    const [viewMode, setViewMode] = useState<ViewMode>('documents');
    const [selectedDocument, setSelectedDocument] = useState<Document | null>(null);

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

    const handleDelete = async (document: Document, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!confirm(`Are you sure you want to delete "${document.original_name}"?`)) return;
        try {
            await deleteMutation.mutateAsync(document.id);
        } catch (error) {
            alert(`Delete failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const handleViewChunks = (document: Document) => {
        setSelectedDocument(document);
        setViewMode('chunks');
    };

    const handleViewPreview = (document: Document, e: React.MouseEvent) => {
        e.stopPropagation();
        setSelectedDocument(document);
        setViewMode('preview');
    };

    const handleDownload = async (document: Document, e: React.MouseEvent) => {
        e.stopPropagation();
        try {
            const token = localStorage.getItem('access_token');
            const response = await fetch(`${API_BASE_URL}${API_ENDPOINTS.DOCUMENT.DOWNLOAD(document.id)}`, {
                headers: {
                    'Authorization': `Bearer ${token}`,
                },
            });

            if (!response.ok) {
                throw new Error('Download failed');
            }

            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = window.document.createElement('a');
            a.href = url;
            a.download = document.original_name;
            window.document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            window.document.body.removeChild(a);
        } catch (error) {
            alert(`Download failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    };

    const handleBackToDocuments = () => {
        setViewMode('documents');
        setSelectedDocument(null);
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
                {/* Upload Area - only show when viewing documents */}
                {viewMode === 'documents' && (
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
                )}

                {/* Uploading Files */}
                {uploadingFiles.length > 0 && viewMode === 'documents' && (
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
                                                <circle className="opacity-25" cx="12" cy="12" r="10"
                                                        stroke="currentColor" strokeWidth="4" fill="none"/>
                                                <path className="opacity-75" fill="currentColor"
                                                      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/>
                                            </svg>
                                            Uploading...
                                        </span>
                                    )}
                                    {uploadingFile.status === 'processing' && (
                                        <span className="inline-flex items-center gap-2 text-sm text-yellow-500">
                                            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                                                <circle className="opacity-25" cx="12" cy="12" r="10"
                                                        stroke="currentColor" strokeWidth="4" fill="none"/>
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

                {/* Documents View */}
                {viewMode === 'documents' && (
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
                                        <th className="text-right px-6 py-3 text-xs font-medium text-secondary-500 dark:text-secondary-400 uppercase tracking-wider">
                                            Actions
                                        </th>
                                    </tr>
                                    </thead>
                                    <tbody className="divide-y divide-secondary-200 dark:divide-navy-700">
                                    {documentsData.items.map((document) => (
                                        <tr
                                            key={document.id}
                                            className="hover:bg-navy-50 dark:hover:bg-navy-700/50 cursor-pointer"
                                            onClick={() => document.status === 'completed' && document.chunk_count > 0 && handleViewChunks(document)}
                                        >
                                            <td className="px-6 py-4">
                                                <div className="flex items-center gap-3">
                                                    <div
                                                        className="flex-shrink-0 w-8 h-8 bg-primary-100 dark:bg-primary-900/30 rounded flex items-center justify-center">
                                                        <span
                                                            className="text-xs font-medium text-primary-600 dark:text-primary-400 uppercase">
                                                            {getFileIcon(document.mime_type).slice(0, 3)}
                                                        </span>
                                                    </div>
                                                    <div className="flex flex-col">
                                                        <span
                                                            className="text-sm font-medium text-navy-900 dark:text-navy-100 truncate max-w-xs">
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
                                                            <circle className="opacity-25" cx="12" cy="12" r="10"
                                                                    stroke="currentColor" strokeWidth="4" fill="none"/>
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
                                                {document.status === 'completed' && document.chunk_count > 0 ? (
                                                    <button
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            handleViewChunks(document);
                                                        }}
                                                        className="text-sm text-primary-500 hover:text-primary-600 dark:text-primary-400 dark:hover:text-primary-300 font-medium"
                                                    >
                                                        {document.chunk_count} chunks
                                                    </button>
                                                ) : (
                                                    <span
                                                        className="text-sm text-secondary-600 dark:text-secondary-400">
                                                        {document.chunk_count}
                                                    </span>
                                                )}
                                            </td>
                                            <td className="px-6 py-4">
                                                <span className="text-sm text-secondary-600 dark:text-secondary-400">
                                                    {formatFileSize(document.file_size)}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 text-right">
                                                <div className="flex items-center justify-end gap-3">
                                                    <button
                                                        onClick={(e) => handleViewPreview(document, e)}
                                                        className="text-primary-500 hover:text-primary-600 dark:text-primary-400 dark:hover:text-primary-300 text-sm font-medium"
                                                        title="Preview"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24"
                                                             stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round"
                                                                  strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                                                            <path strokeLinecap="round" strokeLinejoin="round"
                                                                  strokeWidth={2}
                                                                  d="M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z"/>
                                                        </svg>
                                                    </button>
                                                    <button
                                                        onClick={(e) => handleDownload(document, e)}
                                                        className="text-primary-500 hover:text-primary-600 dark:text-primary-400 dark:hover:text-primary-300 text-sm font-medium"
                                                        title="Download"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24"
                                                             stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round"
                                                                  strokeWidth={2}
                                                                  d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
                                                        </svg>
                                                    </button>
                                                    <button
                                                        onClick={(e) => handleDelete(document, e)}
                                                        disabled={deleteMutation.isPending}
                                                        className="text-red-500 hover:text-red-600 dark:text-red-400 dark:hover:text-red-300 text-sm font-medium disabled:opacity-50"
                                                        title="Delete"
                                                    >
                                                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24"
                                                             stroke="currentColor">
                                                            <path strokeLinecap="round" strokeLinejoin="round"
                                                                  strokeWidth={2}
                                                                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                                                        </svg>
                                                    </button>
                                                </div>
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

                        {/* Documents Pagination */}
                        {documentsData && documentsData.total > documentsData.page_size && (
                            <div className="mt-4 flex justify-center">
                                <div className="text-sm text-secondary-500 dark:text-secondary-400">
                                    Showing {documentsData.items.length} of {documentsData.total} documents
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Chunks View */}
                {viewMode === 'chunks' && selectedDocument && (
                    <ChunksView
                        document={selectedDocument}
                        onBack={handleBackToDocuments}
                    />
                )}

                {/* Preview View */}
                {viewMode === 'preview' && selectedDocument && (
                    <PreviewView
                        document={selectedDocument}
                        onBack={handleBackToDocuments}
                    />
                )}
            </div>
        </div>
    );
}
