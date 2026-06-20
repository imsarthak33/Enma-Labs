'use client';

import { useState, useRef, useEffect } from 'react';
import { Search, X, ChevronDown, SlidersHorizontal } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface FilterOption {
  value: string;
  label: string;
  count?: number;
}

interface FilterDropdownProps {
  label: string;
  options: FilterOption[];
  value: string | null;
  onChange: (value: string | null) => void;
}

function FilterDropdown({ label, options, value, onChange }: FilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  const selectedLabel = options.find((o) => o.value === value)?.label || label;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className={`
          flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-medium transition-all duration-200
          ${value
            ? 'bg-accent/10 border border-accent/20 text-accent'
            : 'bg-white/5 border border-white/10 text-white/50 hover:text-white hover:bg-white/10'
          }
        `}
      >
        <span>{value ? selectedLabel : label}</span>
        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.95 }}
            transition={{ duration: 0.15 }}
            className="absolute top-full mt-2 left-0 z-50 min-w-[180px] bg-black/95 backdrop-blur-xl border border-white/10 rounded-2xl p-1.5 shadow-2xl"
          >
            <button
              onClick={() => { onChange(null); setOpen(false); }}
              className={`w-full text-left px-3 py-2 rounded-xl text-xs transition-all ${!value ? 'text-accent bg-accent/5' : 'text-white/50 hover:text-white hover:bg-white/5'}`}
            >
              All
            </button>
            {options.map((option) => (
              <button
                key={option.value}
                onClick={() => { onChange(option.value); setOpen(false); }}
                className={`w-full text-left px-3 py-2 rounded-xl text-xs transition-all flex items-center justify-between ${
                  value === option.value ? 'text-accent bg-accent/5' : 'text-white/50 hover:text-white hover:bg-white/5'
                }`}
              >
                <span>{option.label}</span>
                {option.count !== undefined && (
                  <span className="text-[10px] text-white/20 font-mono">{option.count}</span>
                )}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

interface FilterBarProps {
  search: string;
  onSearchChange: (v: string) => void;
  searchPlaceholder?: string;
  filters: {
    key: string;
    label: string;
    options: FilterOption[];
    value: string | null;
    onChange: (v: string | null) => void;
  }[];
  resultCount?: number;
  children?: React.ReactNode;
}

export function FilterBar({ search, onSearchChange, searchPlaceholder, filters, resultCount, children }: FilterBarProps) {
  const activeFiltersCount = filters.filter((f) => f.value !== null).length;

  return (
    <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 mb-6">
      {/* Search */}
      <div className="flex items-center gap-2 bg-white/5 border border-white/10 rounded-full px-4 py-2 w-full sm:w-72 transition-all focus-within:border-accent/30 focus-within:bg-white/[0.07]">
        <Search className="w-4 h-4 text-white/30 shrink-0" />
        <input
          type="text"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder={searchPlaceholder || 'Search...'}
          className="bg-transparent border-none outline-none text-sm w-full placeholder:text-white/20"
        />
        {search && (
          <button onClick={() => onSearchChange('')} className="text-white/30 hover:text-white">
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1 text-white/20 mr-1">
          <SlidersHorizontal className="w-3.5 h-3.5" />
          {activeFiltersCount > 0 && (
            <span className="text-[10px] font-bold text-accent bg-accent/10 px-1.5 py-0.5 rounded-full">
              {activeFiltersCount}
            </span>
          )}
        </div>
        {filters.map((filter) => (
          <FilterDropdown
            key={filter.key}
            label={filter.label}
            options={filter.options}
            value={filter.value}
            onChange={filter.onChange}
          />
        ))}
      </div>

      {/* Right side */}
      <div className="sm:ml-auto flex items-center gap-3">
        {resultCount !== undefined && (
          <span className="text-xs text-white/30">
            {resultCount} result{resultCount !== 1 ? 's' : ''}
          </span>
        )}
        {children}
      </div>
    </div>
  );
}
