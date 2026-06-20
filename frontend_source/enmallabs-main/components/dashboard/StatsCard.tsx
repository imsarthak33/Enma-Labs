'use client';

import { useEffect, useState, useRef } from 'react';
import { motion } from 'framer-motion';

interface StatsCardProps {
  label: string;
  value: number;
  icon: React.ComponentType<{ className?: string }>;
  color: string;
  prefix?: string;
  suffix?: string;
  loading?: boolean;
}

function useCountUp(target: number, duration: number = 1200, enabled: boolean = true) {
  const [count, setCount] = useState(0);
  const startTime = useRef<number | null>(null);
  const rafId = useRef<number>(0);

  useEffect(() => {
    if (!enabled || target === 0) {
      setCount(target);
      return;
    }

    startTime.current = null;
    const animate = (timestamp: number) => {
      if (!startTime.current) startTime.current = timestamp;
      const progress = Math.min((timestamp - startTime.current) / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
      setCount(Math.round(eased * target));
      if (progress < 1) {
        rafId.current = requestAnimationFrame(animate);
      }
    };
    rafId.current = requestAnimationFrame(animate);

    return () => cancelAnimationFrame(rafId.current);
  }, [target, duration, enabled]);

  return count;
}

export function StatsCard({ label, value, icon: Icon, color, prefix = '', suffix = '', loading = false }: StatsCardProps) {
  const animatedValue = useCountUp(value, 1200, !loading);

  if (loading) {
    return (
      <div className="glass-card rounded-[24px] p-6 relative overflow-hidden">
        <div className="flex items-center justify-between mb-6">
          <div className="w-12 h-12 rounded-2xl bg-white/5 animate-pulse" />
          <div className="w-12 h-4 bg-white/5 rounded animate-pulse" />
        </div>
        <div className="h-3 w-20 bg-white/5 rounded animate-pulse mb-2" />
        <div className="h-8 w-16 bg-white/10 rounded animate-pulse" />
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      className="glass-card rounded-[24px] p-6 group hover:border-white/20 transition-all duration-300 shadow-[0_0_0_transparent] hover:shadow-[0_15px_40px_rgba(255,78,0,0.06)] relative overflow-hidden cursor-default"
    >
      <div className="absolute top-0 right-0 w-32 h-32 bg-accent/5 blur-[40px] rounded-full pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity duration-500" />
      <div className="flex items-center justify-between mb-6">
        <div className="w-12 h-12 rounded-2xl bg-white/5 flex items-center justify-center border border-white/10 group-hover:border-white/20 transition-all">
          <Icon className={`w-6 h-6 ${color}`} />
        </div>
      </div>
      <p className="text-white/40 text-xs font-medium uppercase tracking-wider mb-1">{label}</p>
      <h3 className="text-3xl font-serif font-medium tracking-tight">
        {prefix}{animatedValue.toLocaleString('en-IN')}{suffix}
      </h3>
    </motion.div>
  );
}
