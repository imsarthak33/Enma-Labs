'use client';

import React from 'react';
import Link from 'next/link';
import { Magnetic } from './FramerAnimations';

interface GlassButtonProps {
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
  className?: string;
  variant?: 'primary' | 'secondary' | 'accent' | 'white';
  type?: 'button' | 'submit' | 'reset';
  disabled?: boolean;
}

export function GlassButton({ 
  href, 
  onClick, 
  children, 
  className = '', 
  variant = 'primary',
  type = 'button',
  disabled = false
}: GlassButtonProps) {
  const baseStyles = "relative px-6 py-3 rounded-full font-bold text-[13px] transition-all duration-300 flex items-center justify-center gap-2 overflow-hidden group active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed";
  
  const variants = {
    primary: "bg-white/10 backdrop-blur-xl border border-white/20 text-white hover:bg-white/20 hover:border-white/30 shadow-[0_0_20px_rgba(255,255,255,0.05)]",
    secondary: "bg-black/20 backdrop-blur-xl border border-white/10 text-white/70 hover:text-white hover:bg-black/40 hover:border-white/20",
    accent: "bg-accent/20 backdrop-blur-xl border border-accent/30 text-white hover:bg-accent/30 hover:border-accent/50 shadow-[0_0_20px_rgba(255,78,0,0.15)]",
    white: "bg-white/90 backdrop-blur-xl border border-white text-black hover:bg-white transition-all shadow-[0_0_30px_rgba(255,255,255,0.1)]",
  };

  const content = (
    <>
      {/* Shimmer Effect */}
      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent -translate-x-full group-hover:animate-shimmer pointer-events-none" />
      {/* Spotlight Hover Effect */}
      <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.15),transparent_60%)] pointer-events-none" />
      <span className="relative z-10 flex items-center gap-2">{children}</span>
    </>
  );

  let element;

  if (href) {
    const isExternal = href.startsWith('http') || href.startsWith('mailto:');
    if (isExternal) {
      element = (
        <a 
          href={href} 
          target="_blank" 
          rel="noopener noreferrer" 
          className={`${baseStyles} ${variants[variant]} ${className}`}
        >
          {content}
        </a>
      );
    } else {
      element = (
        <Link href={href} className={`${baseStyles} ${variants[variant]} ${className}`}>
          {content}
        </Link>
      );
    }
  } else {
    element = (
      <button 
        type={type}
        onClick={onClick} 
        disabled={disabled}
        className={`${baseStyles} ${variants[variant]} ${className}`}
      >
        {content}
      </button>
    );
  }

  return <Magnetic intensity={0.15}>{element}</Magnetic>;
}
