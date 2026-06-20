'use client';

interface StatusBadgeProps {
  status: string;
  size?: 'sm' | 'md';
}

const statusConfig: Record<string, { dot: string; text: string; bg: string; label: string }> = {
  extracted: { dot: 'bg-emerald-400', text: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/20', label: 'Extracted' },
  failed: { dot: 'bg-red-400', text: 'text-red-400', bg: 'bg-red-400/10 border-red-400/20', label: 'Failed' },
  received: { dot: 'bg-blue-400', text: 'text-blue-400', bg: 'bg-blue-400/10 border-blue-400/20', label: 'Received' },
  processing: { dot: 'bg-amber-400 animate-pulse', text: 'text-amber-400', bg: 'bg-amber-400/10 border-amber-400/20', label: 'Processing' },
  // Intent tags
  gst_query: { dot: 'bg-violet-400', text: 'text-violet-400', bg: 'bg-violet-400/10 border-violet-400/20', label: 'GST Query' },
  consultant_query: { dot: 'bg-blue-400', text: 'text-blue-400', bg: 'bg-blue-400/10 border-blue-400/20', label: 'Consultant' },
  general_chat: { dot: 'bg-white/40', text: 'text-white/50', bg: 'bg-white/5 border-white/10', label: 'Chat' },
  news_search: { dot: 'bg-cyan-400', text: 'text-cyan-400', bg: 'bg-cyan-400/10 border-cyan-400/20', label: 'News' },
  golden_correction: { dot: 'bg-amber-400', text: 'text-amber-400', bg: 'bg-amber-400/10 border-amber-400/20', label: 'Correction' },
  audit: { dot: 'bg-orange-400', text: 'text-orange-400', bg: 'bg-orange-400/10 border-orange-400/20', label: 'Audit' },
  task: { dot: 'bg-indigo-400', text: 'text-indigo-400', bg: 'bg-indigo-400/10 border-indigo-400/20', label: 'Task' },
  // Document types
  purchase_invoice: { dot: 'bg-blue-400', text: 'text-blue-400', bg: 'bg-blue-400/10 border-blue-400/20', label: 'Purchase Invoice' },
  sale_invoice: { dot: 'bg-emerald-400', text: 'text-emerald-400', bg: 'bg-emerald-400/10 border-emerald-400/20', label: 'Sale Invoice' },
  bank_statement: { dot: 'bg-purple-400', text: 'text-purple-400', bg: 'bg-purple-400/10 border-purple-400/20', label: 'Bank Statement' },
  unknown: { dot: 'bg-white/30', text: 'text-white/40', bg: 'bg-white/5 border-white/10', label: 'Unknown' },
  chat: { dot: 'bg-white/30', text: 'text-white/40', bg: 'bg-white/5 border-white/10', label: 'Chat' },
};

export function StatusBadge({ status, size = 'sm' }: StatusBadgeProps) {
  const config = statusConfig[status] || statusConfig['unknown'];
  const sizeClasses = size === 'sm'
    ? 'px-2.5 py-0.5 text-[10px]'
    : 'px-3 py-1 text-[11px]';

  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border font-bold uppercase tracking-wider ${config.bg} ${config.text} ${sizeClasses}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${config.dot}`} />
      {config.label}
    </span>
  );
}

export function getStatusConfig(status: string) {
  return statusConfig[status] || statusConfig['unknown'];
}
