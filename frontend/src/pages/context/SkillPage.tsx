import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FolderOpen, Sparkles, Loader2, Trash2, Pencil, CheckCircle2, Circle } from 'lucide-react';
import { useSkills, useUpdateSkill, useDeleteSkill, useUploadSkillFolder } from '@/hooks/useSkills';
import { generateSkillFilesRoute } from '@/constants/routes';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { SkillUpdate, Skill } from '@/types/skill';

// Parse YAML frontmatter from SKILL.md content
function parseFrontmatter(content: string): { name?: string; description?: string; tags?: string[] } {
  if (!content.startsWith('---')) return {};
  const end = content.indexOf('\n---', 3);
  if (end === -1) return {};
  const fmStr = content.slice(3, end).trim();
  const result: { name?: string; description?: string; tags?: string[] } = {};
  for (const line of fmStr.split('\n')) {
    const colonIdx = line.indexOf(':');
    if (colonIdx === -1) continue;
    const key = line.slice(0, colonIdx).trim();
    const val = line.slice(colonIdx + 1).trim();
    if (key === 'name' || key === 'title') result.name = val.replace(/^["']|["']$/g, '');
    else if (key === 'description') result.description = val.replace(/^["']|["']$/g, '');
    else if (key === 'tags') {
      // handle "tags: [a, b]" or "tags: a, b"
      const cleaned = val.replace(/^\[|\]$/g, '');
      result.tags = cleaned.split(',').map((t) => t.trim()).filter(Boolean);
    }
  }
  return result;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function SkillFileList({ files }: { files: Record<string, { s3_key: string; size: number; content_type?: string | null }> }) {
  const entries = Object.entries(files);
  if (entries.length === 0) return null;
  return (
    <div className="space-y-1">
      {entries.map(([path, meta]) => {
        const name = path.split('/').pop() ?? path;
        const isMain = name.toUpperCase() === 'SKILL.MD';
        return (
          <div key={path} className="flex items-center gap-2 text-[11px]">
            <span className={cn('font-mono truncate flex-1 min-w-0', isMain ? 'text-foreground font-medium' : 'text-muted-foreground')}>
              {name}
              {isMain && <span className="ml-1.5 text-[9px] bg-primary/10 text-primary px-1 rounded">main</span>}
            </span>
            {meta.content_type && (
              <span className="text-[9px] px-1 rounded bg-muted text-muted-foreground/70 shrink-0">{meta.content_type.split('/').pop()}</span>
            )}
            <span className="text-muted-foreground/50 tabular-nums shrink-0">{formatBytes(meta.size)}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── File tree helpers ──────────────────────────────────────────────────────

interface TreeNode {
  name: string;
  /** Full webkitRelativePath value, matching folderPaths entries. */
  fullPath: string;
  isDir: boolean;
  children: TreeNode[];
}

function buildFileTree(paths: string[]): TreeNode[] {
  const rootChildren: TreeNode[] = [];
  for (const path of paths) {
    const parts = path.split('/');
    let cur = rootChildren;
    for (let i = 1; i < parts.length; i++) {
      const isLast = i === parts.length - 1;
      const name = parts[i];
      const fullPath = parts.slice(0, i + 1).join('/');
      let node = cur.find((n) => n.name === name);
      if (!node) {
        node = { name, fullPath, isDir: !isLast, children: [] };
        cur.push(node);
      }
      if (!isLast) cur = node.children;
    }
  }
  return rootChildren;
}

function getLeafPaths(node: TreeNode): string[] {
  if (!node.isDir) return [node.fullPath];
  return node.children.flatMap(getLeafPaths);
}

function IndeterminateCheckbox({
  checked, indeterminate, onChange, disabled,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: () => void;
  disabled?: boolean;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={onChange}
      className="size-3 rounded cursor-pointer disabled:cursor-default accent-primary shrink-0"
    />
  );
}

function FileTreeNode({
  node, selected, onToggleFile, onToggleDir, level = 0,
}: {
  node: TreeNode;
  selected: Set<string>;
  onToggleFile: (path: string) => void;
  onToggleDir: (leafPaths: string[], allSelected: boolean) => void;
  level?: number;
}) {
  const pl = level * 14;
  if (!node.isDir) {
    const isMain = node.name.toUpperCase() === 'SKILL.MD';
    const checked = isMain || selected.has(node.fullPath);
    return (
      <div style={{ paddingLeft: pl }} className="flex items-center gap-1.5 py-[3px] group">
        <IndeterminateCheckbox checked={checked} disabled={isMain} onChange={() => onToggleFile(node.fullPath)} />
        <span className={cn('text-[11px] font-mono', isMain ? 'text-foreground font-semibold' : 'text-muted-foreground group-hover:text-foreground transition-colors')}>
          {node.name}
          {isMain && <span className="ml-1.5 text-[9px] bg-primary/10 text-primary px-1 rounded">main</span>}
        </span>
      </div>
    );
  }
  const leafPaths = getLeafPaths(node);
  const selCount = leafPaths.filter((p) => selected.has(p)).length;
  const allSel = selCount === leafPaths.length;
  const someSel = selCount > 0 && !allSel;
  return (
    <div>
      <div style={{ paddingLeft: pl }} className="flex items-center gap-1.5 py-[3px] group">
        <IndeterminateCheckbox checked={allSel} indeterminate={someSel} onChange={() => onToggleDir(leafPaths, allSel)} />
        <span className="text-[11px] font-mono text-foreground/80 group-hover:text-foreground transition-colors font-medium">{node.name}/</span>
        <span className="text-[10px] text-muted-foreground/40 tabular-nums">{selCount}/{leafPaths.length}</span>
      </div>
      {node.children.map((child) => (
        <FileTreeNode key={child.fullPath} node={child} selected={selected} onToggleFile={onToggleFile} onToggleDir={onToggleDir} level={level + 1} />
      ))}
    </div>
  );
}

// ── Page component ─────────────────────────────────────────────────────────

const INITIAL_EDIT_FORM: SkillUpdate = { name: '', description: '', content: '', tags: [] };

export default function SkillPage() {
  const navigate = useNavigate();
  const [showEditModal, setShowEditModal] = useState(false);
  const [showFolderModal, setShowFolderModal] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [formData, setFormData] = useState<SkillUpdate>(INITIAL_EDIT_FORM);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState('');
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);

  // Folder upload state
  const folderInputRef = useRef<HTMLInputElement>(null);
  const [folderFiles, setFolderFiles] = useState<File[]>([]);
  const [folderPaths, setFolderPaths] = useState<string[]>([]);
  const [selectedFilePaths, setSelectedFilePaths] = useState<Set<string>>(new Set());
  const [folderMeta, setFolderMeta] = useState<{ name?: string; description?: string; tags?: string[] }>({});
  const [folderTagInput, setFolderTagInput] = useState('');
  const [folderExtraTags, setFolderExtraTags] = useState<string[]>([]);

  const { data: skillData, isLoading } = useSkills({
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const updateMutation = useUpdateSkill();
  const deleteMutation = useDeleteSkill();
  const uploadFolderMutation = useUploadSkillFolder();

  const skills = skillData?.items ?? [];
  const allTags = Array.from(new Set(skills.flatMap((s) => s.tags ?? []))).sort();

  const addTagToForm = () => {
    if (tagInput.trim() && !formData.tags?.includes(tagInput.trim())) {
      setFormData({ ...formData, tags: [...(formData.tags || []), tagInput.trim()] });
      setTagInput('');
    }
  };
  const removeTagFromForm = (tag: string) =>
    setFormData({ ...formData, tags: formData.tags?.filter((t) => t !== tag) || [] });

  const openEditModal = (skill: Skill) => {
    setEditingSkill(skill);
    setFormData({ name: skill.name, description: skill.description || '', content: '', tags: skill.tags || [] });
    setShowEditModal(true);
  };
  const closeModal = () => {
    setShowEditModal(false);
    setFormData(INITIAL_EDIT_FORM); setEditingSkill(null);
  };

  const handleUpdate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingSkill) return;
    try { await updateMutation.mutateAsync({ id: editingSkill.id, data: formData }); closeModal(); }
    catch (error) { alert(`Update failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };
  const handleDelete = async (skill: Skill, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete "${skill.name}"?`)) return;
    try { await deleteMutation.mutateAsync(skill.id); }
    catch (error) { alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleModeToggle = (m: ViewMode) => {
    setViewMode(m);
    if (m === 'list') setOpenItemId(null);
  };

  const resetFolderState = () => {
    setFolderFiles([]);
    setFolderPaths([]);
    setSelectedFilePaths(new Set());
    setFolderMeta({});
    setFolderExtraTags([]);
    setFolderTagInput('');
  };

  const handleFolderSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files ?? []);
    if (selected.length === 0) return;
    const paths = selected.map((f) => (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name);
    setFolderFiles(selected);
    setFolderPaths(paths);
    setSelectedFilePaths(new Set(paths)); // select all by default
    setFolderExtraTags([]);
    setFolderTagInput('');

    const skillMdIdx = paths.findIndex((p) => p.split('/').pop()?.toUpperCase() === 'SKILL.MD');
    if (skillMdIdx !== -1) {
      const reader = new FileReader();
      reader.onload = (ev) => setFolderMeta(parseFrontmatter(ev.target?.result as string));
      reader.readAsText(selected[skillMdIdx]);
    } else {
      setFolderMeta({});
    }
  };

  const handleToggleFile = (path: string) => {
    setSelectedFilePaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const handleToggleDir = (leafPaths: string[], allSelected: boolean) => {
    setSelectedFilePaths((prev) => {
      const next = new Set(prev);
      if (allSelected) {
        // Uncheck all except SKILL.md (always required)
        leafPaths.forEach((p) => {
          if (p.split('/').pop()?.toUpperCase() !== 'SKILL.MD') next.delete(p);
        });
      } else {
        leafPaths.forEach((p) => next.add(p));
      }
      return next;
    });
  };

  const handleSelectAll = () => setSelectedFilePaths(new Set(folderPaths));
  const handleDeselectAll = () => {
    // Keep only SKILL.md entries
    setSelectedFilePaths(new Set(folderPaths.filter((p) => p.split('/').pop()?.toUpperCase() === 'SKILL.MD')));
  };

  const handleFolderUpload = async () => {
    if (folderFiles.length === 0) return;
    // Always include SKILL.md even if somehow unchecked
    const skillMdSet = new Set(folderPaths.filter((p) => p.split('/').pop()?.toUpperCase() === 'SKILL.MD'));
    const filteredEntries = folderFiles
      .map((f, i) => ({ file: f, path: folderPaths[i] }))
      .filter(({ path }) => selectedFilePaths.has(path) || skillMdSet.has(path));

    const allTags = [...(folderMeta.tags ?? []), ...folderExtraTags];
    try {
      await uploadFolderMutation.mutateAsync({
        files: filteredEntries.map((e) => e.file),
        paths: filteredEntries.map((e) => e.path),
        tags: allTags.length > 0 ? allTags.join(',') : undefined,
      });
      setShowFolderModal(false);
      resetFolderState();
    } catch (error) {
      alert(`Upload failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  if (isLoading) {
    return <div className="flex items-center justify-center py-16"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Sparkles className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Skills</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">{skills.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
          <Button size="sm" variant="outline" onClick={() => setShowFolderModal(true)}>
            <FolderOpen className="size-3.5 mr-1" />Upload Folder
          </Button>
        </div>
      </div>

      {/* Tag filter bar */}
      {allTags.length > 0 && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {selectedTags.length > 0 && (
            <button type="button" onClick={() => setSelectedTags([])}
              className="text-[10px] px-2 py-1 rounded-full border border-border text-muted-foreground hover:text-foreground transition-colors">
              Clear
            </button>
          )}
          {allTags.map((tag) => (
            <button key={tag} type="button"
              onClick={() => setSelectedTags((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag])}
              className={cn('text-[10px] px-2.5 py-1 rounded-full border font-medium transition-colors',
                selectedTags.includes(tag)
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-muted/50 text-muted-foreground border-border/50 hover:border-foreground/30'
              )}>
              {tag}
            </button>
          ))}
        </div>
      )}

      {skills.length > 0 ? viewMode === 'card' ? (
        /* ── Card grid ── */
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {skills.map((skill) => (
            <div key={skill.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors">
              <div className="flex items-start gap-2.5">
                <span className={cn('size-2 rounded-full mt-1 shrink-0', skill.has_embedding ? 'bg-violet-500' : 'bg-muted-foreground/30')} />
                <p className="text-sm font-semibold leading-snug flex-1 min-w-0 truncate">{skill.name}</p>
              </div>
              {skill.description && <p className="text-xs text-muted-foreground line-clamp-2">{skill.description}</p>}
              {skill.tags && skill.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {skill.tags.slice(0, 4).map((tag) => (
                    <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{tag}</span>
                  ))}
                  {skill.tags.length > 4 && <span className="text-[10px] text-muted-foreground/60">+{skill.tags.length - 4}</span>}
                </div>
              )}
              {skill.files && Object.keys(skill.files).length > 0 && (
                <div className="pt-2 border-t border-border/40">
                  <p className="text-[10px] text-muted-foreground mb-1.5 uppercase tracking-wide font-medium">Files</p>
                  <SkillFileList files={skill.files} />
                </div>
              )}
              <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                <span className={cn('inline-flex items-center gap-0.5', skill.has_embedding ? 'text-violet-500' : 'text-muted-foreground/50')}>
                  {skill.has_embedding ? <CheckCircle2 className="size-3" /> : <Circle className="size-3" />}
                </span>
                <span className="ml-auto">{formatRelativeTime(skill.created_at)}</span>
              </div>
              <div className="flex gap-2 pt-2 border-t border-border/40">
                {skill.files && Object.keys(skill.files).length > 0 && (
                  <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => navigate(generateSkillFilesRoute(skill.id))}>
                    <FolderOpen className="size-3.5" />Open
                  </Button>
                )}
                <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => openEditModal(skill)}>
                  <Pencil className="size-3.5" />Edit
                </Button>
                <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                  disabled={deleteMutation.isPending} onClick={(e) => handleDelete(skill, e)}>
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {skills.map((skill) => {
            const indexedDot = (
              <span className={cn('size-2 rounded-full shrink-0', skill.has_embedding ? 'bg-violet-500' : 'bg-muted-foreground/30')} />
            );
            const tagChips = skill.tags && skill.tags.length > 0 ? (
              <div className="flex items-center gap-1 shrink-0">
                {skill.tags.slice(0, 3).map((tag) => (
                  <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{tag}</span>
                ))}
                {skill.tags.length > 3 && <span className="text-[10px] text-muted-foreground/60">+{skill.tags.length - 3}</span>}
              </div>
            ) : null;

            const rowHeader = (
              <>
                {indexedDot}
                <span className="text-sm font-medium flex-1 min-w-0 truncate">{skill.name}</span>
                {tagChips}
              </>
            );

            if (viewMode === 'list') {
              return (
                <div key={skill.id} className="group relative flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors cursor-pointer"
                  onClick={() => skill.files && Object.keys(skill.files).length > 0 && navigate(generateSkillFilesRoute(skill.id))}>
                  {indexedDot}
                  <span className="text-sm font-medium flex-1 min-w-0 truncate">{skill.name}</span>
                  {tagChips}
                  <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted transition-colors" onClick={() => openEditModal(skill)}>
                      <Pencil className="size-3.5 text-muted-foreground" />
                    </button>
                    <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-destructive/10 transition-colors" onClick={(e) => handleDelete(skill, e)}>
                      <Trash2 className="size-3.5 text-destructive/70" />
                    </button>
                  </div>
                  {/* Hover tooltip */}
                  <div className="absolute right-2 top-full mt-1 z-50 w-80 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                    <div className="space-y-2 text-xs">
                      {skill.description && <p className="text-foreground/80 leading-relaxed">{skill.description}</p>}
                      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                        <span>Indexed</span><span className="text-foreground">{skill.has_embedding ? 'Yes' : 'No'}</span>
                        <span>Created</span><span className="text-foreground">{formatRelativeTime(skill.created_at)}</span>
                      </div>
                      {skill.files && Object.keys(skill.files).length > 0 && (
                        <div className="pt-1.5 border-t border-border/50">
                          <p className="text-[10px] text-muted-foreground uppercase tracking-wide font-medium mb-1">
                            Files ({Object.keys(skill.files).length})
                          </p>
                          <SkillFileList files={skill.files} />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              );
            }

            // Drawer mode
            return (
              <AccordionItem
                key={skill.id}
                isOpen={openItemId === skill.id}
                onToggle={() => setOpenItemId(openItemId === skill.id ? null : skill.id)}
                header={rowHeader}
                detail={
                  <div className="space-y-3">
                    {skill.description && <p className="text-xs text-foreground/80 leading-relaxed">{skill.description}</p>}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      <span className="text-muted-foreground">Indexed</span>
                      <span className="flex items-center gap-1.5">
                        <span className={cn('size-1.5 rounded-full', skill.has_embedding ? 'bg-violet-500' : 'bg-muted-foreground/30')} />
                        {skill.has_embedding ? 'Yes' : 'No'}
                      </span>
                      {skill.tags && skill.tags.length > 0 && (
                        <><span className="text-muted-foreground">Tags</span><span>{skill.tags.join(', ')}</span></>
                      )}
                      <span className="text-muted-foreground">Created</span>
                      <span>{formatRelativeTime(skill.created_at)}</span>
                    </div>
                    {skill.files && Object.keys(skill.files).length > 0 && (
                      <div className="pt-2 border-t border-border/40">
                        <p className="text-[10px] text-muted-foreground mb-1.5 uppercase tracking-wide font-medium">
                          Files ({Object.keys(skill.files).length})
                        </p>
                        <SkillFileList files={skill.files} />
                      </div>
                    )}
                    <div className="flex gap-2 pt-2 border-t border-border/40">
                      {skill.files && Object.keys(skill.files).length > 0 && (
                        <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => navigate(generateSkillFilesRoute(skill.id))}>
                          <FolderOpen className="size-3.5" />Open Files
                        </Button>
                      )}
                      <Button size="sm" variant="outline" className="flex-1 gap-1.5" onClick={() => openEditModal(skill)}>
                        <Pencil className="size-3.5" />Edit
                      </Button>
                      <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                        disabled={deleteMutation.isPending} onClick={(e) => handleDelete(skill, e)}>
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                }
              />
            );
          })}
        </div>
      ) : (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Sparkles className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No skills yet</p>
          <Button size="sm" variant="outline" onClick={() => setShowFolderModal(true)}>
            <FolderOpen className="size-3.5 mr-1" />Upload Folder
          </Button>
        </div>
      )}

      {/* Folder Upload Dialog */}
      {(() => {
        const fileTree = buildFileTree(folderPaths);
        const nonMainPaths = folderPaths.filter((p) => p.split('/').pop()?.toUpperCase() !== 'SKILL.MD');
        const selectedCount = nonMainPaths.filter((p) => selectedFilePaths.has(p)).length;
        const totalUpload = selectedCount + (folderPaths.some((p) => p.split('/').pop()?.toUpperCase() === 'SKILL.MD') ? 1 : 0);
        return (
          <Dialog open={showFolderModal} onOpenChange={(open) => { if (!open) { setShowFolderModal(false); resetFolderState(); } }}>
            <DialogContent className="max-w-lg">
              <DialogHeader><DialogTitle>Upload Skill Folder</DialogTitle></DialogHeader>
              <div className="space-y-4 py-1">
                <p className="text-xs text-muted-foreground">
                  Select a folder containing a <code className="bg-muted px-1 rounded">SKILL.md</code> file.
                  Check the files and sub-folders you want to include.
                </p>

                <div>
                  <input
                    ref={folderInputRef}
                    type="file"
                    className="hidden"
                    // @ts-expect-error webkitdirectory is not in typings
                    webkitdirectory=""
                    multiple
                    onChange={handleFolderSelect}
                  />
                  <Button variant="outline" className="w-full" onClick={() => folderInputRef.current?.click()}>
                    <FolderOpen className="size-4 mr-2" />
                    {folderFiles.length > 0 ? `${folderFiles.length} files in folder` : 'Choose Folder'}
                  </Button>
                </div>

                {folderFiles.length > 0 && (
                  <>
                    {/* File tree with checkboxes */}
                    <div className="rounded-lg border border-border bg-muted/30">
                      <div className="flex items-center justify-between px-3 pt-2.5 pb-1.5 border-b border-border/50">
                        <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                          Files — {totalUpload}/{folderPaths.length} selected
                        </span>
                        <div className="flex gap-2">
                          <button type="button" onClick={handleSelectAll} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">All</button>
                          <button type="button" onClick={handleDeselectAll} className="text-[10px] text-muted-foreground hover:text-foreground transition-colors">None</button>
                        </div>
                      </div>
                      <div className="p-3 max-h-52 overflow-y-auto">
                        {fileTree.map((node) => (
                          <FileTreeNode
                            key={node.fullPath}
                            node={node}
                            selected={selectedFilePaths}
                            onToggleFile={handleToggleFile}
                            onToggleDir={handleToggleDir}
                          />
                        ))}
                      </div>
                    </div>

                    {!folderMeta.name && (
                      <p className="text-xs text-destructive">No SKILL.md found or missing <code>name</code> frontmatter field.</p>
                    )}

                    {/* Extracted metadata */}
                    {folderMeta.name && (
                      <div className="rounded-lg border border-border bg-card p-3 space-y-2">
                        <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">Extracted from SKILL.md</p>
                        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                          <span className="text-muted-foreground">Name</span>
                          <span className="font-medium">{folderMeta.name}</span>
                          {folderMeta.description && (
                            <><span className="text-muted-foreground">Description</span><span>{folderMeta.description}</span></>
                          )}
                          {folderMeta.tags && folderMeta.tags.length > 0 && (
                            <><span className="text-muted-foreground">Tags</span>
                            <div className="flex flex-wrap gap-1">
                              {folderMeta.tags.map((t) => <span key={t} className="px-1.5 py-0.5 rounded bg-muted text-[10px]">{t}</span>)}
                            </div></>
                          )}
                        </div>
                      </div>
                    )}

                    {/* Extra tags */}
                    <div className="space-y-1.5">
                      <Label className="text-xs">Additional Tags</Label>
                      <div className="flex gap-2">
                        <Input
                          value={folderTagInput}
                          onChange={(e) => setFolderTagInput(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              const t = folderTagInput.trim();
                              if (t && !folderExtraTags.includes(t)) setFolderExtraTags([...folderExtraTags, t]);
                              setFolderTagInput('');
                            }
                          }}
                          placeholder="Add tag..."
                          className="flex-1"
                        />
                        <Button type="button" variant="secondary" size="sm" onClick={() => {
                          const t = folderTagInput.trim();
                          if (t && !folderExtraTags.includes(t)) setFolderExtraTags([...folderExtraTags, t]);
                          setFolderTagInput('');
                        }}>Add</Button>
                      </div>
                      {folderExtraTags.length > 0 && (
                        <div className="flex flex-wrap gap-1.5 mt-1">
                          {folderExtraTags.map((t) => (
                            <Badge key={t} variant="secondary" className="gap-1 text-xs">{t}
                              <button type="button" onClick={() => setFolderExtraTags(folderExtraTags.filter((x) => x !== t))} className="ml-1 hover:text-foreground">×</button>
                            </Badge>
                          ))}
                        </div>
                      )}
                    </div>
                  </>
                )}
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => { setShowFolderModal(false); resetFolderState(); }}>Cancel</Button>
                <Button disabled={!folderMeta.name || uploadFolderMutation.isPending} onClick={handleFolderUpload}>
                  {uploadFolderMutation.isPending
                    ? <><Loader2 className="size-4 animate-spin mr-2" />Uploading...</>
                    : `Upload ${totalUpload} file${totalUpload !== 1 ? 's' : ''}`}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        );
      })()}

      {/* Edit Dialog */}
      <Dialog open={showEditModal} onOpenChange={(open) => { if (!open) closeModal(); }}>
        <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Edit Skill</DialogTitle></DialogHeader>
          <form id="skill-form" onSubmit={handleUpdate} className="space-y-4">
            <div className="space-y-1.5"><Label>Name *</Label>
              <Input value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder="e.g., Python Best Practices" required /></div>
            <div className="space-y-1.5"><Label>Description</Label>
              <Input value={formData.description ?? ''} onChange={(e) => setFormData({ ...formData, description: e.target.value })} placeholder="Brief description" /></div>
            <div className="space-y-1.5">
              <Label>Tags</Label>
              <div className="flex gap-2">
                <Input value={tagInput} onChange={(e) => setTagInput(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTagToForm())} placeholder="Add a tag..." className="flex-1" />
                <Button type="button" variant="secondary" onClick={addTagToForm}>Add</Button>
              </div>
              {formData.tags && formData.tags.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-2">
                  {formData.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="gap-1">{tag}
                      <button type="button" onClick={() => removeTagFromForm(tag)} className="hover:text-foreground ml-1">×</button>
                    </Badge>
                  ))}
                </div>
              )}
            </div>
            <div className="space-y-1.5">
              <Label>Content (Markdown)</Label>
              <Textarea
                value={formData.content ?? ''}
                onChange={(e) => setFormData({ ...formData, content: e.target.value })}
                rows={16}
                placeholder="Leave blank to keep existing content unchanged"
                className="font-mono text-sm"
              />
              <p className="text-xs text-muted-foreground">Content is stored in the Context table. Leave blank to keep existing.</p>
            </div>
          </form>
          <DialogFooter>
            <Button variant="outline" onClick={closeModal}>Cancel</Button>
            <Button type="submit" form="skill-form" disabled={updateMutation.isPending}>
              {updateMutation.isPending ? 'Saving...' : 'Update Skill'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
