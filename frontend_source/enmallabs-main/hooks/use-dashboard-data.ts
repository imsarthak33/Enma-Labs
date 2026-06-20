'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { getSupabase } from '@/lib/supabase';
import type { RealtimeChannel } from '@supabase/supabase-js';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface DashboardStats {
  totalDocuments: number;
  extractedDocuments: number;
  failedDocuments: number;
  pendingDocuments: number;
  totalChatLogs: number;
  gstQueries: number;
  consultantQueries: number;
  processingRate: number; // percentage
  documentTypes: Record<string, number>;
  intentTags: Record<string, number>;
  totalAmount: number;
}

export interface DocumentRecord {
  id: string;
  ca_firm_id: string;
  client_id: string | null;
  document_type: string | null;
  processing_status: string;
  source_file_ids: any;
  extraction_data: any;
  tax_verdict: any;
  verification_result: any;
  filing_period_month: number | null;
  filing_period_year: number | null;
  processing_time_ms: number | null;
  created_at: string;
  updated_at: string | null;
  // Computed from extraction_data for UI compatibility
  original_file_name: string;
  total_amount: number | null;
  vendor_name: string | null;
  buyer_name: string | null;
  vendor_gstin: string | null;
  buyer_gstin: string | null;
  invoice_number: string | null;
  transaction_date: string | null;
  intent_tag: string | null;
}

export interface ChatLogRecord {
  id: string;
  ca_firm_id: string | null;
  client_id: string | null;
  chat_id: number | null;
  role: string;
  content: string;
  message_id: number | null;
  metadata: any;
  created_at: string;
  // Mapped fields for UI compatibility
  user_message: string;
  bot_response: string;
  intent_tag: string | null;
  is_error: boolean | null;
  response_time_ms: number | null;
}

export interface ClientInfo {
  name: string;
  documentCount: number;
  totalAmount: number;
  lastActivity: string;
  documentTypes: string[];
  statuses: string[];
}

export interface DocumentFilters {
  search: string;
  documentType: string | null;
  processingStatus: string | null;
  dateFrom: string | null;
  dateTo: string | null;
  vendor: string | null;
}

export interface FirmData {
  id: string;
  supabase_user_id: string | null;
  firebase_uid: string | null;
  firm_name: string;
  ca_name: string;
  email: string;
  phone: string | null;
  telegram_chat_id: string | null;
  telegram_linked_at: string | null;
  admin_chat_id: number | null;
  is_active: boolean;
  subscription_plan: string;
  subscription_tier: string;
  onboarding_completed: boolean;
  dpa_consented: boolean;
  dpa_consented_at: string | null;
  data_training_consent: boolean;
  created_at: string;
  updated_at: string | null;
  known_groups: any;
}

// ─── Helpers for extraction_data ─────────────────────────────────────────────

function extractDocField(doc: any, field: string): any {
  // Try extraction_data first, then top-level
  if (doc.extraction_data && typeof doc.extraction_data === 'object') {
    if (doc.extraction_data[field] !== undefined) return doc.extraction_data[field];
  }
  return doc[field] ?? null;
}

function mapDocumentRecord(raw: any): DocumentRecord {
  const ed = raw.extraction_data || {};
  return {
    ...raw,
    original_file_name: ed.file_name || ed.original_file_name || `Document ${raw.document_type || 'Unknown'}`,
    total_amount: ed.total_amount ?? ed.invoice_value ?? ed.amount ?? null,
    vendor_name: ed.vendor_name ?? ed.supplier_name ?? ed.seller_name ?? null,
    buyer_name: ed.buyer_name ?? ed.customer_name ?? null,
    vendor_gstin: ed.vendor_gstin ?? ed.supplier_gstin ?? null,
    buyer_gstin: ed.buyer_gstin ?? ed.customer_gstin ?? null,
    invoice_number: ed.invoice_number ?? ed.invoice_no ?? null,
    transaction_date: ed.transaction_date ?? ed.invoice_date ?? null,
    intent_tag: ed.intent_tag ?? raw.document_type ?? null,
  };
}

// ─── useFirmData ─────────────────────────────────────────────────────────────

export function useFirmData(supabaseUserId: string | null) {
  const [firmData, setFirmData] = useState<FirmData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const channelRef = useRef<RealtimeChannel | null>(null);

  useEffect(() => {
    if (!supabaseUserId) {
      setLoading(false);
      return;
    }

    const supabase = getSupabase();

    const fetchFirm = async () => {
      setLoading(true);
      const { data, error: err } = await supabase
        .from('ca_firms')
        .select('*')
        .eq('supabase_user_id', supabaseUserId)
        .single();

      if (err) {
        setError(err.message);
      } else {
        setFirmData(data);
      }
      setLoading(false);
    };

    fetchFirm();

    // Realtime subscription
    channelRef.current = supabase
      .channel(`firm-${supabaseUserId}`)
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'ca_firms',
          filter: `supabase_user_id=eq.${supabaseUserId}`,
        },
        (payload) => {
          if (payload.eventType === 'UPDATE' || payload.eventType === 'INSERT') {
            setFirmData(payload.new as FirmData);
          }
        }
      )
      .subscribe();

    return () => {
      channelRef.current?.unsubscribe();
    };
  }, [supabaseUserId]);

  return { firmData, loading, error };
}

// ─── useDashboardStats ───────────────────────────────────────────────────────

export function useDashboardStats(firmId: string | null) {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(true);
  const channelRef = useRef<RealtimeChannel | null>(null);

  const fetchStats = useCallback(async () => {
    if (!firmId) return;
    const supabase = getSupabase();

    const [docsResult, chatsResult] = await Promise.all([
      supabase.from('documents').select('id, document_type, processing_status, extraction_data').eq('ca_firm_id', firmId),
      supabase.from('conversations').select('id, role, metadata').eq('ca_firm_id', firmId),
    ]);

    const docs = docsResult.data || [];
    const chats = chatsResult.data || [];

    const documentTypes: Record<string, number> = {};
    const statuses: Record<string, number> = {};
    let totalAmount = 0;

    docs.forEach((d: any) => {
      const dt = d.document_type || 'unknown';
      documentTypes[dt] = (documentTypes[dt] || 0) + 1;
      statuses[d.processing_status] = (statuses[d.processing_status] || 0) + 1;
      const ed = d.extraction_data || {};
      const amt = ed.total_amount ?? ed.invoice_value ?? ed.amount ?? 0;
      if (amt) totalAmount += amt;
    });

    const intentTags: Record<string, number> = {};
    chats.forEach((c: any) => {
      const meta = c.metadata || {};
      const tag = meta.intent_tag || c.role || 'unknown';
      intentTags[tag] = (intentTags[tag] || 0) + 1;
    });

    const extracted = statuses['extracted'] || statuses['completed'] || statuses['processed'] || 0;
    const failed = statuses['failed'] || statuses['error'] || 0;
    const received = statuses['received'] || statuses['pending'] || statuses['processing'] || 0;

    // Count unique conversations (pairs of user+assistant messages)
    const totalConversations = chats.filter((c: any) => c.role === 'user').length;

    setStats({
      totalDocuments: docs.length,
      extractedDocuments: extracted,
      failedDocuments: failed,
      pendingDocuments: received,
      totalChatLogs: totalConversations,
      gstQueries: intentTags['gst_query'] || 0,
      consultantQueries: intentTags['consultant_query'] || 0,
      processingRate: docs.length > 0 ? Math.round((extracted / docs.length) * 100 * 10) / 10 : 0,
      documentTypes,
      intentTags,
      totalAmount,
    });
    setLoading(false);
  }, [firmId]);

  useEffect(() => {
    fetchStats();

    if (!firmId) return;
    const supabase = getSupabase();

    // Realtime: re-fetch stats when documents or conversations change
    channelRef.current = supabase
      .channel(`stats-${firmId}`)
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'documents', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchStats()
      )
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'conversations', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchStats()
      )
      .subscribe();

    return () => {
      channelRef.current?.unsubscribe();
    };
  }, [firmId, fetchStats]);

  return { stats, loading };
}

// ─── useDocuments ────────────────────────────────────────────────────────────

export function useDocuments(firmId: string | null, filters: DocumentFilters) {
  const [documents, setDocuments] = useState<DocumentRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [totalCount, setTotalCount] = useState(0);
  const channelRef = useRef<RealtimeChannel | null>(null);

  const fetchDocuments = useCallback(async () => {
    if (!firmId) return;
    const supabase = getSupabase();
    setLoading(true);

    let query = supabase
      .from('documents')
      .select('*', { count: 'exact' })
      .eq('ca_firm_id', firmId)
      .order('created_at', { ascending: false });

    if (filters.documentType) {
      query = query.eq('document_type', filters.documentType);
    }
    if (filters.processingStatus) {
      query = query.eq('processing_status', filters.processingStatus);
    }
    if (filters.dateFrom) {
      query = query.gte('created_at', filters.dateFrom);
    }
    if (filters.dateTo) {
      query = query.lte('created_at', filters.dateTo + 'T23:59:59');
    }

    const { data, error, count } = await query;
    if (!error) {
      setDocuments((data || []).map(mapDocumentRecord));
      setTotalCount(count || 0);
    }
    setLoading(false);
  }, [firmId, filters]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  // Realtime subscription
  useEffect(() => {
    if (!firmId) return;
    const supabase = getSupabase();

    channelRef.current = supabase
      .channel(`docs-${firmId}`)
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'documents', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchDocuments()
      )
      .subscribe();

    return () => {
      channelRef.current?.unsubscribe();
    };
  }, [firmId, fetchDocuments]);

  return { documents, loading, totalCount, refetch: fetchDocuments };
}

// ─── useClients ──────────────────────────────────────────────────────────────

export function useClients(firmId: string | null) {
  const [clients, setClients] = useState<ClientInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const channelRef = useRef<RealtimeChannel | null>(null);

  const fetchClients = useCallback(async () => {
    if (!firmId) return;
    const supabase = getSupabase();

    const [docsResult, clientsResult] = await Promise.all([
      supabase
        .from('documents')
        .select('document_type, processing_status, extraction_data, created_at')
        .eq('ca_firm_id', firmId),
      supabase
        .from('clients')
        .select('trade_name, legal_name, created_at')
        .eq('ca_firm_id', firmId)
    ]);

    const docs = docsResult.data;
    const manualClients = clientsResult.data;

    const clientMap = new Map<string, ClientInfo>();

    // Add all manually created clients
    if (manualClients) {
      manualClients.forEach((c: any) => {
        const name = c.trade_name || c.legal_name || 'Unknown';
        if (name === 'Unknown') return;
        clientMap.set(name, {
          name,
          documentCount: 0,
          totalAmount: 0,
          lastActivity: c.created_at,
          documentTypes: [],
          statuses: [],
        });
      });
    }

    // Aggregate from documents
    if (docs) {
      docs.forEach((doc: any) => {
        const ed = doc.extraction_data || {};
        const name = ed.vendor_name || ed.supplier_name || ed.buyer_name || ed.customer_name || 'Unknown';
        if (name === 'Unknown') return;

        const amt = ed.total_amount ?? ed.invoice_value ?? ed.amount ?? 0;
        const existing = clientMap.get(name);
        if (existing) {
          existing.documentCount++;
          existing.totalAmount += amt;
          if (doc.created_at > existing.lastActivity) {
            existing.lastActivity = doc.created_at;
          }
          if (doc.document_type && !existing.documentTypes.includes(doc.document_type)) {
            existing.documentTypes.push(doc.document_type);
          }
          if (doc.processing_status && !existing.statuses.includes(doc.processing_status)) {
            existing.statuses.push(doc.processing_status);
          }
        } else {
          clientMap.set(name, {
            name,
            documentCount: 1,
            totalAmount: amt,
            lastActivity: doc.created_at,
            documentTypes: doc.document_type ? [doc.document_type] : [],
            statuses: doc.processing_status ? [doc.processing_status] : [],
          });
        }
      });
    }

    setClients(
      Array.from(clientMap.values()).sort((a, b) => b.documentCount - a.documentCount)
    );
    setLoading(false);
  }, [firmId]);

  useEffect(() => {
    fetchClients();
  }, [fetchClients]);

  useEffect(() => {
    if (!firmId) return;
    const supabase = getSupabase();

    channelRef.current = supabase
      .channel(`clients-${firmId}`)
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'documents', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchClients()
      )
      .on(
        'postgres_changes',
        { event: '*', schema: 'public', table: 'clients', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchClients()
      )
      .subscribe();

    return () => {
      channelRef.current?.unsubscribe();
    };
  }, [firmId, fetchClients]);

  return { clients, loading, refetch: fetchClients };
}

// ─── useRecentActivity ───────────────────────────────────────────────────────

export interface ActivityItem {
  id: string;
  type: 'document' | 'chat';
  title: string;
  description: string;
  status: string;
  timestamp: string;
  metadata?: any;
}

export function useRecentActivity(firmId: string | null, limit: number = 10) {
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [loading, setLoading] = useState(true);
  const channelRef = useRef<RealtimeChannel | null>(null);

  const fetchActivity = useCallback(async () => {
    if (!firmId) return;
    const supabase = getSupabase();

    const [docsResult, chatsResult] = await Promise.all([
      supabase
        .from('documents')
        .select('id, document_type, processing_status, extraction_data, created_at')
        .eq('ca_firm_id', firmId)
        .order('created_at', { ascending: false })
        .limit(limit),
      supabase
        .from('conversations')
        .select('id, role, content, metadata, created_at')
        .eq('ca_firm_id', firmId)
        .eq('role', 'user')
        .order('created_at', { ascending: false })
        .limit(limit),
    ]);

    const docActivities: ActivityItem[] = (docsResult.data || []).map((d: any) => {
      const ed = d.extraction_data || {};
      const name = ed.vendor_name || ed.supplier_name || ed.file_name || `${formatDocType(d.document_type)} Document`;
      return {
        id: d.id,
        type: 'document' as const,
        title: name,
        description: `${formatDocType(d.document_type)} — ${ed.invoice_number || ed.file_name || 'Processing...'}`,
        status: d.processing_status,
        timestamp: d.created_at,
        metadata: { total_amount: ed.total_amount ?? ed.invoice_value, document_type: d.document_type },
      };
    });

    const chatActivities: ActivityItem[] = (chatsResult.data || []).map((c: any) => ({
      id: c.id,
      type: 'chat' as const,
      title: c.content.length > 60 ? c.content.substring(0, 60) + '...' : c.content,
      description: `Chat message`,
      status: c.metadata?.intent_tag || 'chat',
      timestamp: c.created_at,
    }));

    const combined = [...docActivities, ...chatActivities]
      .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
      .slice(0, limit);

    setActivities(combined);
    setLoading(false);
  }, [firmId, limit]);

  useEffect(() => {
    fetchActivity();
  }, [fetchActivity]);

  useEffect(() => {
    if (!firmId) return;
    const supabase = getSupabase();

    channelRef.current = supabase
      .channel(`activity-${firmId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'documents', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchActivity()
      )
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'conversations', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchActivity()
      )
      .subscribe();

    return () => {
      channelRef.current?.unsubscribe();
    };
  }, [firmId, fetchActivity]);

  return { activities, loading };
}

// ─── useChatLogs ─────────────────────────────────────────────────────────────

export function useChatLogs(firmId: string | null, limit: number = 50) {
  const [chatLogs, setChatLogs] = useState<ChatLogRecord[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!firmId) return;
    const supabase = getSupabase();

    const fetchLogs = async () => {
      // Fetch from conversations table and pair user/assistant messages
      const { data } = await supabase
        .from('conversations')
        .select('*')
        .eq('ca_firm_id', firmId)
        .order('created_at', { ascending: false })
        .limit(limit * 2); // fetch extra since we pair user+assistant

      const rawMessages = data || [];

      // Group into user-assistant pairs for the chat UI
      const pairedLogs: ChatLogRecord[] = [];
      const userMessages = rawMessages.filter((m: any) => m.role === 'user');
      const assistantMessages = rawMessages.filter((m: any) => m.role === 'assistant');

      // Simple pairing: match by proximity in time
      userMessages.forEach((userMsg: any) => {
        // Find the closest assistant response after this user message
        const response = assistantMessages.find((a: any) =>
          new Date(a.created_at) >= new Date(userMsg.created_at)
        );

        pairedLogs.push({
          id: userMsg.id,
          ca_firm_id: userMsg.ca_firm_id,
          client_id: userMsg.client_id,
          chat_id: userMsg.chat_id,
          role: userMsg.role,
          content: userMsg.content,
          message_id: userMsg.message_id,
          metadata: userMsg.metadata,
          created_at: userMsg.created_at,
          user_message: userMsg.content,
          bot_response: response?.content || 'Processing...',
          intent_tag: userMsg.metadata?.intent_tag || null,
          is_error: userMsg.metadata?.is_error || false,
          response_time_ms: response?.metadata?.response_time_ms || null,
        });
      });

      setChatLogs(pairedLogs.slice(0, limit));
      setLoading(false);
    };

    fetchLogs();

    const channel = supabase
      .channel(`chats-${firmId}`)
      .on(
        'postgres_changes',
        { event: 'INSERT', schema: 'public', table: 'conversations', filter: `ca_firm_id=eq.${firmId}` },
        () => fetchLogs()
      )
      .subscribe();

    return () => {
      channel.unsubscribe();
    };
  }, [firmId, limit]);

  return { chatLogs, loading };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

export function formatDocType(type: string | null): string {
  if (!type) return 'Unknown';
  return type
    .split('_')
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

export function formatRelativeTime(dateString: string): string {
  const now = new Date();
  const date = new Date(dateString);
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMins / 60);
  const diffDays = Math.floor(diffHours / 24);

  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  if (diffHours < 24) return `${diffHours}h ago`;
  if (diffDays < 7) return `${diffDays}d ago`;
  return date.toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
}

export function formatCurrency(amount: number): string {
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(amount);
}
