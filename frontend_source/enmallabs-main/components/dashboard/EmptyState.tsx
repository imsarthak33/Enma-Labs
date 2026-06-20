'use client';

import { motion } from 'framer-motion';

interface EmptyStateProps {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  action?: { label: string; onClick: () => void };
}

export function EmptyState({ icon: Icon, title, description, action }: EmptyStateProps) {
  return (
    <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} className="flex flex-col items-center justify-center py-20 text-center">
      <div className="relative mb-8">
        <div className="absolute inset-0 bg-accent/5 blur-[50px] rounded-full" />
        <div className="relative w-20 h-20 rounded-3xl bg-white/[0.03] border border-white/10 flex items-center justify-center">
          <Icon className="w-10 h-10 text-white/15" />
        </div>
      </div>
      <h3 className="font-serif text-2xl font-medium mb-3 text-white/80">{title}</h3>
      <p className="text-sm text-white/40 max-w-md leading-relaxed mb-8">{description}</p>
      {action && (
        <button onClick={action.onClick} className="px-6 py-3 rounded-full bg-accent/20 border border-accent/30 text-white text-sm font-bold hover:bg-accent/30 transition-all active:scale-95">
          {action.label}
        </button>
      )}
    </motion.div>
  );
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="glass-card rounded-[24px] overflow-hidden">
      <div className="px-6 py-4 border-b border-white/5 flex gap-8">
        {[100, 80, 120, 60, 80].map((w, i) => (
          <div key={i} className="h-3 bg-white/5 rounded animate-pulse" style={{ width: w }} />
        ))}
      </div>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="px-6 py-5 border-b border-white/[0.03] flex items-center gap-8">
          <div className="h-4 w-28 bg-white/5 rounded animate-pulse" />
          <div className="h-4 w-20 bg-white/5 rounded animate-pulse" />
          <div className="h-4 w-32 bg-white/[0.03] rounded animate-pulse" />
          <div className="h-5 w-16 bg-white/5 rounded-full animate-pulse" />
        </div>
      ))}
    </div>
  );
}

export function CardGridSkeleton({ cards = 6 }: { cards?: number }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {Array.from({ length: cards }).map((_, i) => (
        <div key={i} className="glass-card rounded-[24px] p-6">
          <div className="flex items-center gap-4 mb-6">
            <div className="w-12 h-12 rounded-2xl bg-white/5 animate-pulse" />
            <div className="flex-1 space-y-2">
              <div className="h-4 w-32 bg-white/10 rounded animate-pulse" />
              <div className="h-3 w-20 bg-white/5 rounded animate-pulse" />
            </div>
          </div>
          <div className="space-y-3">
            <div className="h-3 w-full bg-white/[0.03] rounded animate-pulse" />
            <div className="h-3 w-2/3 bg-white/[0.03] rounded animate-pulse" />
          </div>
        </div>
      ))}
    </div>
  );
}
