import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, FileText, Loader2, ChevronRight } from 'lucide-react';
import { useSkill } from '@/hooks/useSkills';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const EXT_LANG: Record<string, string> = {
  py: 'python', ts: 'typescript', tsx: 'typescript', js: 'javascript',
  jsx: 'javascript', json: 'json', yaml: 'yaml', yml: 'yaml',
  md: 'markdown', txt: 'text', sh: 'shell', toml: 'toml',
};

function extOf(path: string): string {
  return path.split('.').pop()?.toLowerCase() ?? '';
}

function FileContentViewer({
  skillId,
  filePath,
  onBack,
}: {
  skillId: string;
  filePath: string;
  onBack: () => void;
}) {
  const fileName = filePath.split('/').pop() ?? filePath;
  const lang = EXT_LANG[extOf(filePath)];
  const isCode = lang && lang !== 'markdown' && lang !== 'text';

  const { data, isLoading, error } = useQuery({
    queryKey: ['skill-file', skillId, filePath],
    queryFn: async () => {
      const token = localStorage.getItem('access_token');
      const url = `${API_BASE_URL}${API_ENDPOINTS.SKILLS.FILE_CONTENT(skillId, filePath)}`;
      const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res.text();
    },
  });

  return (
    <div>
      <div className="flex items-center gap-3 mb-5">
        <Button variant="ghost" size="icon" onClick={onBack}>
          <ArrowLeft className="size-5" />
        </Button>
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold truncate">{fileName}</h3>
          <p className="text-sm text-muted-foreground font-mono">{filePath}</p>
        </div>
        {lang && (
          <span className="text-xs px-2 py-0.5 rounded bg-muted text-muted-foreground font-mono shrink-0">
            {lang}
          </span>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-16">
          <Loader2 className="size-6 animate-spin text-muted-foreground mx-auto" />
        </div>
      ) : error ? (
        <div className="text-center py-16 bg-card rounded-lg border border-border">
          <p className="text-destructive mb-1">Failed to load file</p>
          <p className="text-sm text-muted-foreground">{error instanceof Error ? error.message : 'Unknown error'}</p>
        </div>
      ) : (
        <div className={cn(
          'rounded-lg border border-border p-5 text-sm leading-relaxed whitespace-pre-wrap break-words overflow-x-auto',
          isCode ? 'bg-zinc-950 text-zinc-200 font-mono' : 'bg-card text-foreground',
        )}>
          {data}
        </div>
      )}
    </div>
  );
}

export default function SkillFilesPage() {
  const { skillId } = useParams<{ skillId: string }>();
  const navigate = useNavigate();
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  const { data: skill, isLoading } = useSkill(skillId ?? '');

  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!skill) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center">
          <h2 className="text-xl font-bold mb-2">Skill Not Found</h2>
          <Button variant="link" onClick={() => navigate('/home')}>Go back to Home</Button>
        </div>
      </div>
    );
  }

  const files = skill.files ? Object.entries(skill.files) : [];

  return (
    <div className="min-h-screen bg-muted/30">
      {/* Header */}
      <div className="bg-card border-b border-border">
        <div className="max-w-5xl mx-auto px-6 py-4 flex items-center gap-4">
          <Button variant="ghost" size="icon" onClick={() => navigate('/home')}>
            <ArrowLeft className="size-6" />
          </Button>
          <div>
            <h1 className="text-xl font-bold">{skill.name}</h1>
            <p className="text-sm text-muted-foreground">
              {files.length} file{files.length !== 1 ? 's' : ''}
              {skill.description && ` · ${skill.description}`}
            </p>
          </div>
        </div>
      </div>

      <div className="max-w-5xl mx-auto px-6 py-8">
        {selectedFile && skillId ? (
          <FileContentViewer
            skillId={skillId}
            filePath={selectedFile}
            onBack={() => setSelectedFile(null)}
          />
        ) : files.length > 0 ? (
          <div className="space-y-1.5">
            {files.map(([path, meta]) => {
              const name = path.split('/').pop() ?? path;
              const isMain = name.toUpperCase() === 'SKILL.MD';
              const lang = EXT_LANG[extOf(path)];
              return (
                <button
                  key={path}
                  type="button"
                  onClick={() => setSelectedFile(path)}
                  className="w-full flex items-center gap-4 px-4 py-3 rounded-lg border border-border bg-card hover:bg-muted/40 hover:border-primary/40 transition-colors text-left group"
                >
                  <div className="size-9 rounded-md bg-primary/10 flex items-center justify-center shrink-0">
                    <FileText className="size-4 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">
                      {name}
                      {isMain && (
                        <span className="ml-2 text-[10px] bg-primary/10 text-primary px-1.5 rounded font-normal">main</span>
                      )}
                    </p>
                    <p className="text-xs text-muted-foreground font-mono truncate">{path}</p>
                  </div>
                  {lang && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-mono shrink-0">
                      {lang}
                    </span>
                  )}
                  <span className="text-xs text-muted-foreground tabular-nums shrink-0">
                    {formatBytes(meta.size)}
                  </span>
                  <ChevronRight className="size-4 text-muted-foreground/40 group-hover:text-muted-foreground transition-colors shrink-0" />
                </button>
              );
            })}
          </div>
        ) : (
          <div className="text-center py-16 bg-card rounded-lg border border-dashed border-border">
            <FileText className="mx-auto size-8 text-muted-foreground/30 mb-3" />
            <p className="text-muted-foreground">No files in this skill</p>
          </div>
        )}
      </div>
    </div>
  );
}
