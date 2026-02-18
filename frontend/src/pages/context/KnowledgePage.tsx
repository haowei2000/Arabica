import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, Loader2, Trash2 } from 'lucide-react';
import { useKnowledgeList, useCreateKnowledge, useDeleteKnowledge } from '@/hooks/useKnowledge';
import { generateKnowledgeDocumentsRoute } from '@/constants/routes';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import type { KnowledgeCreate } from '@/types/knowledge';

const INITIAL_FORM: KnowledgeCreate = {
  name: '',
  description: '',
  provider: 'default',
  indexing_technique: 'high_quality',
  permission: 'private',
};

export default function KnowledgePage() {
  const navigate = useNavigate();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [formData, setFormData] = useState<KnowledgeCreate>(INITIAL_FORM);

  const { data: knowledgeData, isLoading } = useKnowledgeList();
  const createMutation = useCreateKnowledge();
  const deleteMutation = useDeleteKnowledge();

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createMutation.mutateAsync(formData);
      setShowCreateForm(false);
      setFormData(INITIAL_FORM);
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to delete "${name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(id);
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold">Knowledge Bases</h2>
          <p className="text-sm text-muted-foreground mt-1">Manage your knowledge bases for AI context</p>
        </div>
        <Button onClick={() => setShowCreateForm(true)}>+ Create Knowledge</Button>
      </div>

      {knowledgeData?.items && knowledgeData.items.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {knowledgeData.items.map((knowledge) => (
            <div key={knowledge.id} className="bg-card rounded-lg border border-border p-6 hover:shadow-lg transition-shadow">
              <div className="flex items-start justify-between mb-4">
                <div className="flex-1">
                  <h3 className="text-lg font-semibold mb-1">{knowledge.name}</h3>
                  <div className="flex gap-2">
                    <Badge variant={knowledge.status === 'active' ? 'default' : 'secondary'}>
                      {knowledge.status}
                    </Badge>
                    <Badge variant="outline">{knowledge.permission}</Badge>
                  </div>
                </div>
              </div>

              {knowledge.description && (
                <p className="text-sm text-muted-foreground mb-4 line-clamp-2">{knowledge.description}</p>
              )}

              <div className="text-sm text-muted-foreground mb-4 space-y-1">
                <p>Documents: {knowledge.document_count}</p>
                <p>Chunks: {knowledge.chunk_count}</p>
                <p>Technique: {knowledge.indexing_technique}</p>
                <p className="text-xs">Created: {new Date(knowledge.created_at).toLocaleDateString()}</p>
              </div>

              <div className="flex gap-2">
                <Button className="flex-1" size="sm" onClick={() => navigate(generateKnowledgeDocumentsRoute(knowledge.id))}>
                  Manage
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                  disabled={deleteMutation.isPending}
                  onClick={() => handleDelete(knowledge.id, knowledge.name)}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-12">
          <BookOpen className="mx-auto size-12 text-muted-foreground/40 mb-4" />
          <h3 className="text-lg font-medium mb-1">No Knowledge Bases</h3>
          <p className="text-muted-foreground mb-4">Create your first knowledge base to get started</p>
          <Button onClick={() => setShowCreateForm(true)}>Create Knowledge Base</Button>
        </div>
      )}

      {knowledgeData && knowledgeData.total > knowledgeData.page_size && (
        <div className="mt-8 flex justify-center">
          <p className="text-sm text-muted-foreground">
            Showing {knowledgeData.items.length} / {knowledgeData.total} knowledge bases
          </p>
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={showCreateForm} onOpenChange={(open) => { if (!open) { setShowCreateForm(false); setFormData(INITIAL_FORM); } }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create Knowledge Base</DialogTitle>
          </DialogHeader>

          <form id="create-knowledge-form" onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-1.5">
              <Label>Name *</Label>
              <Input
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="e.g., Product Documentation"
                required
              />
            </div>

            <div className="space-y-1.5">
              <Label>Description</Label>
              <Textarea
                value={formData.description || ''}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Describe the purpose of this knowledge base"
                rows={3}
              />
            </div>

            <div className="space-y-1.5">
              <Label>Indexing Technique</Label>
              <Select
                value={formData.indexing_technique}
                onValueChange={(v) => setFormData({ ...formData, indexing_technique: v })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="high_quality">High Quality</SelectItem>
                  <SelectItem value="economy">Economy</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Permission</Label>
              <Select
                value={formData.permission}
                onValueChange={(v) => setFormData({ ...formData, permission: v })}
              >
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="private">Private</SelectItem>
                  <SelectItem value="public">Public</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </form>

          <DialogFooter>
            <Button variant="outline" onClick={() => { setShowCreateForm(false); setFormData(INITIAL_FORM); }}>
              Cancel
            </Button>
            <Button type="submit" form="create-knowledge-form" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating...' : 'Create'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
