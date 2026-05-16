import { useEffect, useMemo, useState } from 'react';
import type { LucideIcon } from 'lucide-react';
import {
  Archive,
  Braces,
  Download,
  Eye,
  File as FileIcon,
  FileAudio2,
  FileCode2,
  FileImage,
  FileJson,
  FileSpreadsheet,
  FileText,
  FileType2,
  FileVideo2,
  Loader2,
  Table2,
  X,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { chatFileService } from '@/services/chatFileService';
import type { ChatFileAttachment } from '@/types/chatFile';
import { cn } from '@/lib/utils';

const TEXT_PREVIEW_LIMIT = 256 * 1024;
const REMOTE_PREVIEW_LIMIT = 10 * 1024 * 1024;

export type ChatFilePreviewTarget =
  | { kind: 'local'; file: File }
  | { kind: 'remote'; attachment: ChatFileAttachment; workspaceId: string };

type PreviewMode = 'image' | 'text' | 'pdf' | 'audio' | 'video' | 'none';

type FileVisual = {
  label: string;
  icon: LucideIcon;
  tone: string;
  bg: string;
};

function formatFileSize(size?: number | null) {
  if (!size) return '';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function extensionFor(name: string) {
  const idx = name.lastIndexOf('.');
  if (idx < 0 || idx === name.length - 1) return '';
  return name.slice(idx + 1).toLowerCase();
}

function getFileVisual(name: string, contentType?: string | null): FileVisual {
  const ext = extensionFor(name);
  const type = contentType?.toLowerCase() ?? '';

  if (type.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) {
    return { label: ext || 'IMG', icon: FileImage, tone: 'text-pink-500', bg: 'bg-pink-500/10 border-pink-500/20' };
  }
  if (type.startsWith('audio/') || ['mp3', 'wav', 'm4a', 'flac'].includes(ext)) {
    return { label: ext || 'AUD', icon: FileAudio2, tone: 'text-violet-500', bg: 'bg-violet-500/10 border-violet-500/20' };
  }
  if (type.startsWith('video/') || ['mp4', 'mov', 'webm', 'mkv'].includes(ext)) {
    return { label: ext || 'VID', icon: FileVideo2, tone: 'text-red-500', bg: 'bg-red-500/10 border-red-500/20' };
  }
  if (type.includes('pdf') || ext === 'pdf') {
    return { label: 'PDF', icon: FileType2, tone: 'text-rose-500', bg: 'bg-rose-500/10 border-rose-500/20' };
  }
  if (type.includes('spreadsheet') || ['xlsx', 'xls'].includes(ext)) {
    return { label: ext || 'SHEET', icon: FileSpreadsheet, tone: 'text-emerald-500', bg: 'bg-emerald-500/10 border-emerald-500/20' };
  }
  if (type.includes('csv') || ext === 'csv') {
    return { label: 'CSV', icon: Table2, tone: 'text-teal-500', bg: 'bg-teal-500/10 border-teal-500/20' };
  }
  if (type.includes('json') || ext === 'json') {
    return { label: 'JSON', icon: FileJson, tone: 'text-amber-500', bg: 'bg-amber-500/10 border-amber-500/20' };
  }
  if (['py', 'ts', 'tsx', 'js', 'jsx', 'css', 'html', 'sql', 'sh'].includes(ext)) {
    return { label: ext || 'CODE', icon: FileCode2, tone: 'text-sky-500', bg: 'bg-sky-500/10 border-sky-500/20' };
  }
  if (['zip', 'tar', 'gz', 'rar', '7z'].includes(ext)) {
    return { label: ext || 'ZIP', icon: Archive, tone: 'text-orange-500', bg: 'bg-orange-500/10 border-orange-500/20' };
  }
  if (type.startsWith('text/') || ['txt', 'md', 'markdown', 'xml', 'yaml', 'yml'].includes(ext)) {
    return { label: ext || 'TXT', icon: FileText, tone: 'text-blue-500', bg: 'bg-blue-500/10 border-blue-500/20' };
  }
  if (['doc', 'docx', 'ppt', 'pptx'].includes(ext)) {
    return { label: ext || 'DOC', icon: FileText, tone: 'text-indigo-500', bg: 'bg-indigo-500/10 border-indigo-500/20' };
  }
  return { label: ext || 'FILE', icon: FileIcon, tone: 'text-muted-foreground', bg: 'bg-muted border-border' };
}

function getPreviewMode(name: string, contentType?: string | null): PreviewMode {
  const ext = extensionFor(name);
  const type = contentType?.toLowerCase() ?? '';

  if (type.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'image';
  if (type.startsWith('audio/') || ['mp3', 'wav', 'm4a', 'flac'].includes(ext)) return 'audio';
  if (type.startsWith('video/') || ['mp4', 'mov', 'webm'].includes(ext)) return 'video';
  if (type.includes('pdf') || ext === 'pdf') return 'pdf';
  if (
    type.startsWith('text/') ||
    type.includes('json') ||
    type.includes('xml') ||
    type.includes('csv') ||
    ['txt', 'md', 'markdown', 'json', 'csv', 'xml', 'yaml', 'yml', 'py', 'ts', 'tsx', 'js', 'jsx', 'css', 'html', 'sql', 'sh'].includes(ext)
  ) {
    return 'text';
  }
  return 'none';
}

function targetMeta(target: ChatFilePreviewTarget) {
  if (target.kind === 'local') {
    return {
      name: target.file.name,
      contentType: target.file.type || null,
      size: target.file.size,
      path: null,
    };
  }
  return {
    name: target.attachment.name,
    contentType: target.attachment.content_type,
    size: target.attachment.size_bytes,
    path: target.attachment.path,
  };
}

export function FileGlyph({
  name,
  contentType,
  className,
}: {
  name: string;
  contentType?: string | null;
  className?: string;
}) {
  const visual = getFileVisual(name, contentType);
  const Icon = visual.icon;
  return (
    <span className={cn('inline-flex items-center gap-1.5 rounded-md border px-2 py-1', visual.bg, className)}>
      <Icon className={cn('size-3.5 shrink-0', visual.tone)} />
      <span className={cn('text-[9px] font-bold uppercase tracking-wide', visual.tone)}>
        {visual.label.slice(0, 7)}
      </span>
    </span>
  );
}

export function SelectedFilePreviewList({
  files,
  onRemove,
  onPreview,
}: {
  files: File[];
  onRemove: (file: File) => void;
  onPreview: (file: File) => void;
}) {
  if (files.length === 0) return null;

  return (
    <div className="grid gap-2 px-4 pt-3 sm:grid-cols-2">
      {files.map((file) => (
        <div
          key={`${file.name}:${file.size}:${file.lastModified}`}
          className="group flex min-w-0 items-center gap-2 rounded-xl border border-border/70 bg-muted/40 px-3 py-2"
        >
          <FileGlyph name={file.name} contentType={file.type} className="shrink-0" />
          <button
            type="button"
            onClick={() => onPreview(file)}
            className="min-w-0 flex-1 text-left"
            title="Preview file"
          >
            <span className="block truncate text-xs font-medium text-foreground">{file.name}</span>
            <span className="block truncate text-[10px] text-muted-foreground">
              {formatFileSize(file.size) || 'Unknown size'}
            </span>
          </button>
          <button
            type="button"
            onClick={() => onPreview(file)}
            className="size-7 shrink-0 rounded-lg text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
            title="Preview file"
          >
            <Eye className="mx-auto size-3.5" />
          </button>
          <button
            type="button"
            onClick={() => onRemove(file)}
            className="size-7 shrink-0 rounded-lg text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
            title="Remove file"
          >
            <X className="mx-auto size-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

export function ChatAttachmentChips({
  attachments,
  onPreview,
  compact = false,
}: {
  attachments: ChatFileAttachment[];
  onPreview?: (attachment: ChatFileAttachment) => void;
  compact?: boolean;
}) {
  if (attachments.length === 0) return null;

  return (
    <div className={cn('flex flex-wrap gap-2', compact ? 'mt-2' : 'px-4 pt-3')}>
      {attachments.map((attachment) => (
        <button
          key={attachment.id}
          type="button"
          onClick={() => onPreview?.(attachment)}
          className={cn(
            'inline-flex min-w-0 max-w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-xs transition-colors',
            compact
              ? 'border-primary-foreground/20 bg-primary-foreground/10 text-primary-foreground hover:bg-primary-foreground/15'
              : 'border-border/70 bg-muted/50 text-foreground hover:bg-muted',
          )}
          title={attachment.path}
        >
          <FileGlyph
            name={attachment.name}
            contentType={attachment.content_type}
            className={cn(
              'shrink-0 px-1.5 py-0.5',
              compact && 'border-primary-foreground/20 bg-primary-foreground/10',
            )}
          />
          <span className="min-w-0 truncate font-medium">{attachment.name}</span>
          {attachment.size_bytes != null && (
            <span className="shrink-0 opacity-60 tabular-nums">{formatFileSize(attachment.size_bytes)}</span>
          )}
          <Eye className="size-3 shrink-0 opacity-60" />
        </button>
      ))}
    </div>
  );
}

export function ChatFilePreviewDialog({
  target,
  onClose,
}: {
  target: ChatFilePreviewTarget | null;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [textPreview, setTextPreview] = useState<string | null>(null);
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [skipped, setSkipped] = useState(false);

  const meta = useMemo(() => (target ? targetMeta(target) : null), [target]);
  const previewMode = meta ? getPreviewMode(meta.name, meta.contentType) : 'none';
  const visual = meta ? getFileVisual(meta.name, meta.contentType) : null;

  useEffect(() => {
    if (!target || !meta) return;

    const currentTarget = target;
    const currentMeta = meta;
    let cancelled = false;
    let nextObjectUrl: string | null = null;

    async function loadPreview() {
      setLoading(false);
      setTextPreview(null);
      setObjectUrl(null);
      setError(null);
      setTruncated(false);
      setSkipped(false);

      if (previewMode === 'none') {
        return;
      }
      if (currentTarget.kind === 'remote' && currentMeta.size && currentMeta.size > REMOTE_PREVIEW_LIMIT) {
        setSkipped(true);
        return;
      }

      setLoading(true);
      try {
        const blob = currentTarget.kind === 'local'
          ? currentTarget.file
          : await chatFileService.downloadBlob(currentTarget.workspaceId, currentTarget.attachment);

        if (cancelled) return;

        if (previewMode === 'text') {
          const slice = blob.slice(0, TEXT_PREVIEW_LIMIT);
          setTextPreview(await slice.text());
          setTruncated(blob.size > TEXT_PREVIEW_LIMIT);
        } else {
          nextObjectUrl = URL.createObjectURL(blob);
          setObjectUrl(nextObjectUrl);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Preview failed');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadPreview();

    return () => {
      cancelled = true;
      if (nextObjectUrl) URL.revokeObjectURL(nextObjectUrl);
    };
  }, [target, meta, previewMode]);

  const handleDownload = async () => {
    if (!target || !meta) return;
    if (target.kind === 'remote') {
      await chatFileService.downloadFile(target.workspaceId, target.attachment);
      return;
    }

    const url = URL.createObjectURL(target.file);
    const link = document.createElement('a');
    link.href = url;
    link.download = meta.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <Dialog open={!!target} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-w-4xl p-0 gap-0 overflow-hidden" showCloseButton>
        {meta && visual && (
          <>
            <DialogHeader className="border-b border-border px-5 py-4">
              <div className="flex min-w-0 items-start gap-3 pr-8">
                <FileGlyph name={meta.name} contentType={meta.contentType} className="mt-0.5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <DialogTitle className="truncate text-base">{meta.name}</DialogTitle>
                  <DialogDescription className="mt-1 truncate text-xs">
                    {[meta.contentType || 'unknown type', formatFileSize(meta.size), meta.path].filter(Boolean).join(' · ')}
                  </DialogDescription>
                </div>
                <Button type="button" size="sm" variant="outline" className="h-8 gap-1.5" onClick={handleDownload}>
                  <Download className="size-3.5" />
                  Download
                </Button>
              </div>
            </DialogHeader>

            <div className="min-h-[320px] max-h-[70vh] overflow-auto bg-muted/20 p-4">
              {loading && (
                <div className="flex h-80 items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading preview
                </div>
              )}

              {!loading && error && (
                <div className="flex h-80 flex-col items-center justify-center gap-2 rounded-xl border border-border bg-card text-sm text-muted-foreground">
                  <Braces className="size-8 opacity-40" />
                  <span>{error}</span>
                </div>
              )}

              {!loading && skipped && (
                <div className="flex h-80 flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card text-center">
                  <FileGlyph name={meta.name} contentType={meta.contentType} />
                  <div>
                    <p className="text-sm font-medium">Preview skipped for large remote file</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Download the file or ask the agent to inspect its context path with read_context.
                    </p>
                  </div>
                </div>
              )}

              {!loading && !error && !skipped && previewMode === 'text' && textPreview !== null && (
                <div className="rounded-xl border border-border bg-background">
                  <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words p-4 text-xs leading-relaxed text-foreground/85">
                    {textPreview || '(empty file)'}
                  </pre>
                  {truncated && (
                    <div className="border-t border-border px-4 py-2 text-xs text-muted-foreground">
                      Preview truncated to {formatFileSize(TEXT_PREVIEW_LIMIT)}.
                    </div>
                  )}
                </div>
              )}

              {!loading && !error && !skipped && previewMode === 'image' && objectUrl && (
                <div className="flex justify-center rounded-xl border border-border bg-background p-3">
                  <img src={objectUrl} alt={meta.name} className="max-h-[60vh] max-w-full rounded-lg object-contain" />
                </div>
              )}

              {!loading && !error && !skipped && previewMode === 'pdf' && objectUrl && (
                <iframe title={meta.name} src={objectUrl} className="h-[65vh] w-full rounded-xl border border-border bg-background" />
              )}

              {!loading && !error && !skipped && previewMode === 'audio' && objectUrl && (
                <div className="flex h-80 flex-col items-center justify-center gap-4 rounded-xl border border-border bg-card">
                  <FileGlyph name={meta.name} contentType={meta.contentType} />
                  <audio controls src={objectUrl} className="w-full max-w-xl" />
                </div>
              )}

              {!loading && !error && !skipped && previewMode === 'video' && objectUrl && (
                <video controls src={objectUrl} className="max-h-[65vh] w-full rounded-xl border border-border bg-background" />
              )}

              {!loading && !error && !skipped && previewMode === 'none' && (
                <div className="flex h-80 flex-col items-center justify-center gap-3 rounded-xl border border-border bg-card text-center">
                  <FileGlyph name={meta.name} contentType={meta.contentType} />
                  <div>
                    <p className="text-sm font-medium">No inline preview for this file type</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      The original file is available for download and the parsed content is available through read_context when parsing succeeded.
                    </p>
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
