'use client';

import { useState, useMemo, useCallback } from 'react';
import { useDashboard } from '../dashboard-context';
import { useClients, useDocuments, formatRelativeTime, formatCurrency } from '@/hooks/use-dashboard-data';
import type { DocumentFilters } from '@/hooks/use-dashboard-data';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { EmptyState, CardGridSkeleton } from '@/components/dashboard/EmptyState';
import { FilterBar } from '@/components/dashboard/FilterBar';
import { DocumentDetailModal } from '@/components/dashboard/DocumentDetailModal';
import { motion, AnimatePresence } from 'framer-motion';
import { Users, FileText, IndianRupee, Send, Building2, X, ArrowLeft, Eye, UserPlus, Loader2, Trash2, Edit3, Save, Plus } from 'lucide-react';
import { getSupabase } from '@/lib/supabase';

export default function ClientsPage() {
  const { firmData, firmLoading } = useDashboard();
  const { clients, loading, refetch } = useClients(firmData?.id || null);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<string | null>(null);
  const [selectedClient, setSelectedClient] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);

  const filteredClients = useMemo(() => {
    let result = [...clients];
    if (search) { const q = search.toLowerCase(); result = result.filter(c => c.name.toLowerCase().includes(q)); }
    if (sortBy === 'docs') result.sort((a, b) => b.documentCount - a.documentCount);
    else if (sortBy === 'amount') result.sort((a, b) => b.totalAmount - a.totalAmount);
    else if (sortBy === 'recent') result.sort((a, b) => new Date(b.lastActivity).getTime() - new Date(a.lastActivity).getTime());
    return result;
  }, [clients, search, sortBy]);

  const isLoading = firmLoading || loading;

  if (selectedClient) {
    return <ClientDetailView clientName={selectedClient} firmId={firmData?.id || ''} onBack={() => setSelectedClient(null)} />;
  }

  return (
    <div className="max-w-7xl mx-auto">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="font-serif text-3xl font-medium mb-2">Clients</h1>
          <p className="text-white/40 text-sm">{clients.length > 0 ? `${clients.length} client${clients.length !== 1 ? 's' : ''} found` : "Manage your firm's clients"}</p>
        </div>
        <button onClick={() => setShowAddModal(true)} className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-accent/20 border border-accent/30 text-white text-[13px] font-bold hover:bg-accent/30 transition-all active:scale-95">
          <UserPlus className="w-4 h-4" />Add Client
        </button>
      </div>

      {clients.length > 0 && (
        <FilterBar search={search} onSearchChange={setSearch} searchPlaceholder="Search clients by name..."
          filters={[{ key: 'sort', label: 'Sort By', options: [
            { value: 'docs', label: 'Most Documents' }, { value: 'amount', label: 'Highest Value' }, { value: 'recent', label: 'Most Recent' },
          ], value: sortBy, onChange: setSortBy }]} resultCount={filteredClients.length}
        />
      )}

      {isLoading ? <CardGridSkeleton cards={6} /> : clients.length === 0 ? (
        <div className="space-y-6">
          <EmptyState icon={Users} title="No Clients Yet" description="Add clients manually or they'll appear automatically when documents are processed through the Enma bot."
            action={{ label: 'Add Your First Client', onClick: () => setShowAddModal(true) }} />
          <div className="max-w-2xl mx-auto glass-card rounded-[24px] p-8">
            <h3 className="font-serif text-xl font-medium mb-6 text-center">How to Add Clients</h3>
            <div className="space-y-6">
              <HowToStep num="1" title="Add Manually" description='Click "Add Client" above to create a client record directly.' icon={UserPlus} />
              <HowToStep num="2" title="Via Telegram" description="Forward client documents to the Enma bot — clients are auto-detected from invoices." icon={Send} />
              <HowToStep num="3" title="Auto-Detection" description="Enma extracts vendor/buyer names from documents and creates client entries." icon={Building2} />
            </div>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredClients.map((client, i) => (
            <motion.div key={client.name} initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
              whileHover={{ y: -4, transition: { duration: 0.2 } }}
              onClick={() => setSelectedClient(client.name)}
              className="glass-card rounded-[24px] p-6 group hover:border-white/20 transition-all duration-300 cursor-pointer">
              <div className="flex items-start gap-4 mb-5">
                <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-accent/20 to-orange-600/20 border border-accent/20 flex items-center justify-center font-bold text-sm text-accent shrink-0">
                  {client.name.substring(0, 2).toUpperCase()}
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-bold text-white truncate group-hover:text-accent transition-colors">{client.name}</h3>
                  <p className="text-[11px] text-white/30 mt-0.5">Last active {formatRelativeTime(client.lastActivity)}</p>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 mb-4">
                <div className="bg-white/[0.02] rounded-xl p-3 border border-white/5">
                  <div className="flex items-center gap-1.5 mb-1"><FileText className="w-3 h-3 text-white/20" /><span className="text-[10px] text-white/30 uppercase font-bold tracking-wider">Docs</span></div>
                  <p className="text-lg font-serif font-medium">{client.documentCount}</p>
                </div>
                <div className="bg-white/[0.02] rounded-xl p-3 border border-white/5">
                  <div className="flex items-center gap-1.5 mb-1"><IndianRupee className="w-3 h-3 text-white/20" /><span className="text-[10px] text-white/30 uppercase font-bold tracking-wider">Value</span></div>
                  <p className="text-lg font-serif font-medium">{client.totalAmount > 0 ? formatCurrency(client.totalAmount) : '—'}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">{client.documentTypes.map(type => <StatusBadge key={type} status={type} />)}</div>
            </motion.div>
          ))}
        </div>
      )}

      {/* Add Client Modal */}
      <AnimatePresence>
        {showAddModal && <AddClientModal firmId={firmData?.id || ''} onClose={() => setShowAddModal(false)} onSuccess={() => { setShowAddModal(false); refetch(); }} />}
      </AnimatePresence>
    </div>
  );
}

// ─── Client Detail View ──────────────────────────────────────────────────────

function ClientDetailView({ clientName, firmId, onBack }: { clientName: string; firmId: string; onBack: () => void }) {
  const emptyFilters: DocumentFilters = { search: '', documentType: null, processingStatus: null, dateFrom: null, dateTo: null, vendor: clientName };
  const { documents, loading } = useDocuments(firmId, emptyFilters);
  const [selectedDoc, setSelectedDoc] = useState<any>(null);

  const totalAmount = documents.reduce((s, d) => s + (d.total_amount || 0), 0);
  const extracted = documents.filter(d => d.processing_status === 'extracted').length;
  const failed = documents.filter(d => d.processing_status === 'failed').length;

  return (
    <div className="max-w-7xl mx-auto">
      <button onClick={onBack} className="flex items-center gap-2 text-white/40 hover:text-white text-sm mb-6 transition-colors">
        <ArrowLeft className="w-4 h-4" />Back to Clients
      </button>

      <div className="flex items-start gap-6 mb-8">
        <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-accent/20 to-orange-600/20 border border-accent/20 flex items-center justify-center font-bold text-xl text-accent shrink-0">
          {clientName.substring(0, 2).toUpperCase()}
        </div>
        <div>
          <h1 className="font-serif text-3xl font-medium mb-1">{clientName}</h1>
          <p className="text-white/40 text-sm">{documents.length} documents • Total value: {totalAmount > 0 ? formatCurrency(totalAmount) : '₹0'}</p>
        </div>
      </div>

      {/* Client Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 mb-8">
        <div className="glass-card rounded-xl p-4"><p className="text-[10px] text-white/30 uppercase font-bold mb-1">Documents</p><p className="text-2xl font-serif font-medium">{documents.length}</p></div>
        <div className="glass-card rounded-xl p-4"><p className="text-[10px] text-white/30 uppercase font-bold mb-1">Total Value</p><p className="text-2xl font-serif font-medium">{totalAmount > 0 ? formatCurrency(totalAmount) : '—'}</p></div>
        <div className="glass-card rounded-xl p-4"><p className="text-[10px] text-white/30 uppercase font-bold mb-1">Extracted</p><p className="text-2xl font-serif font-medium text-emerald-400">{extracted}</p></div>
        <div className="glass-card rounded-xl p-4"><p className="text-[10px] text-white/30 uppercase font-bold mb-1">Failed</p><p className="text-2xl font-serif font-medium text-red-400">{failed}</p></div>
      </div>

      {/* Documents Table */}
      <h2 className="font-serif text-xl font-medium mb-4">Documents</h2>
      {loading ? (
        <div className="glass-card rounded-[24px] p-8 text-center"><Loader2 className="w-6 h-6 text-accent animate-spin mx-auto" /></div>
      ) : documents.length === 0 ? (
        <div className="glass-card rounded-[24px] p-12"><EmptyState icon={FileText} title="No Documents" description={`No documents found for ${clientName}.`} /></div>
      ) : (
        <div className="glass-card rounded-[24px] overflow-hidden">
          <div className="divide-y divide-white/[0.03]">
            {documents.map((doc, i) => (
              <div key={doc.id} onClick={() => setSelectedDoc(doc)} className="px-6 py-4 flex items-center justify-between hover:bg-white/[0.02] transition-all cursor-pointer group">
                <div className="flex items-center gap-4 min-w-0">
                  <div className="w-10 h-10 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center shrink-0"><FileText className="w-5 h-5 text-blue-400" /></div>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-white truncate group-hover:text-accent transition-colors">{doc.original_file_name}</p>
                    <p className="text-[11px] text-white/30 mt-0.5">{formatRelativeTime(doc.created_at)}</p>
                  </div>
                </div>
                <div className="flex items-center gap-4 shrink-0">
                  <StatusBadge status={doc.document_type || 'unknown'} />
                  <StatusBadge status={doc.processing_status} />
                  <span className="text-sm font-mono text-white/50">{doc.total_amount ? formatCurrency(doc.total_amount) : '—'}</span>
                  <Eye className="w-4 h-4 text-white/20 group-hover:text-white/60" />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <DocumentDetailModal document={selectedDoc} onClose={() => setSelectedDoc(null)} />
    </div>
  );
}

// ─── Add Client Modal ────────────────────────────────────────────────────────

function AddClientModal({ firmId, onClose, onSuccess }: { firmId: string; onClose: () => void; onSuccess: () => void }) {
  const [name, setName] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSave = async () => {
    if (!name.trim()) { setError('Client name is required'); return; }
    setSaving(true);
    setError('');
    try {
      const supabase = getSupabase();
      const { error: err } = await supabase.from('clients').insert([{ trade_name: name.trim(), ca_firm_id: firmId }]);
      if (err) throw err;
      onSuccess();
    } catch (e: any) {
      setError(e.message || 'Failed to add client');
    } finally { setSaving(false); }
  };

  return (
    <>
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50" onClick={onClose} />
      <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 0.95 }} className="fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="w-full max-w-md bg-black/95 backdrop-blur-xl border border-white/10 rounded-3xl p-8" onClick={e => e.stopPropagation()}>
          <div className="flex items-center justify-between mb-6">
            <h2 className="font-serif text-xl font-medium">Add New Client</h2>
            <button onClick={onClose} className="w-8 h-8 rounded-lg bg-white/5 flex items-center justify-center text-white/40 hover:text-white"><X className="w-4 h-4" /></button>
          </div>
          {error && <div className="mb-4 p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-400 text-sm">{error}</div>}
          <div className="space-y-4">
            <div>
              <label className="text-[10px] font-bold uppercase tracking-wider text-white/30 block mb-2">Client / Vendor Name</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Nykaa E-Retail Limited"
                className="w-full bg-white/[0.04] border border-white/10 rounded-xl text-white text-sm px-4 py-3 focus:outline-none focus:border-accent/50 focus:ring-2 focus:ring-accent/10 transition-all placeholder:text-white/20" />
            </div>
          </div>
          <div className="flex items-center gap-3 mt-6">
            <button onClick={handleSave} disabled={saving} className="flex-1 flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent/20 border border-accent/30 text-white text-sm font-bold hover:bg-accent/30 transition-all disabled:opacity-50">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}{saving ? 'Adding...' : 'Add Client'}
            </button>
            <button onClick={onClose} className="px-5 py-3 rounded-xl bg-white/5 border border-white/10 text-sm text-white/50 hover:text-white transition-all">Cancel</button>
          </div>
        </div>
      </motion.div>
    </>
  );
}

function HowToStep({ num, title, description, icon: Icon }: { num: string; title: string; description: string; icon: any }) {
  return (
    <div className="flex items-start gap-4">
      <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center shrink-0"><Icon className="w-5 h-5 text-white/30" /></div>
      <div><h4 className="text-sm font-bold text-white">{title}</h4><p className="text-xs text-white/40 mt-1 leading-relaxed">{description}</p></div>
    </div>
  );
}
