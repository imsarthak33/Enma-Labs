'use client';

import React, { useState, useEffect, useRef } from 'react';
import { motion, useScroll, useSpring, useTransform, useInView, Variants } from 'framer-motion';

/**
 * TextScramble Component
 * Replicates the "ProTextType" effect.
 * Animates text by cycling through random characters when it comes into view.
 */
interface TextScrambleProps {
  text: string;
  className?: string;
  duration?: number;
  delay?: number;
  once?: boolean;
}

const GLYPHS = '__';

export const TextScramble: React.FC<TextScrambleProps> = ({ 
  text, 
  className = '', 
  duration = 1.2,
  delay = 0,
  once = true
}) => {
  const [displayText, setDisplayText] = useState(text);
  const [isAnimating, setIsAnimating] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);
  const isInView = useInView(containerRef, { once, amount: 0.5 });
  const timerRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (!isInView) return;

    const startAnimation = () => {
      let iteration = 0;
      const totalIterations = Math.floor(duration * 60);
      
      if (timerRef.current) clearInterval(timerRef.current);

      timerRef.current = setInterval(() => {
        setDisplayText(
          text
            .split('')
            .map((char, index) => {
              if (char === ' ') return ' ';
              if (index < iteration / (totalIterations / text.length)) {
                return text[index];
              }
              return GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
            })
            .join('')
        );

        if (iteration >= totalIterations) {
          clearInterval(timerRef.current!);
          setDisplayText(text);
          setIsAnimating(false);
        }

        iteration += 1;
      }, 1000 / 60);
    };

    const timeout = setTimeout(() => {
      setIsAnimating(true);
      startAnimation();
    }, delay * 1000);

    return () => {
      clearTimeout(timeout);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [isInView, text, duration, delay]);

  return (
    <span ref={containerRef} className={`${className} inline-block min-h-[1em]`}>
      {displayText}
    </span>
  );
};

/**
 * ScrollProgressBar Component
 * Replicates the "Progress-Bar" effect.
 * Shows a progress bar that fills as the user scrolls through a section.
 */
interface ScrollProgressBarProps {
  containerRef: React.RefObject<HTMLElement | null>;
  className?: string;
}

export const ScrollProgressBar: React.FC<ScrollProgressBarProps> = ({ 
  containerRef,
  className = '' 
}) => {
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start center", "end center"]
  });

  const scaleY = useSpring(scrollYProgress, {
    stiffness: 100,
    damping: 30,
    restDelta: 0.001
  });

  const opacity = useTransform(scrollYProgress, [0, 0.05, 0.95, 1], [0, 1, 1, 0]);
  const dotY = useTransform(scrollYProgress, [0, 1], ["0%", "100%"]);

  return (
    <motion.div 
      style={{ opacity }}
      className={`absolute left-0 top-0 bottom-0 w-[1px] bg-white/5 overflow-visible ${className}`}
    >
      {/* Track Glow */}
      <div className="absolute inset-0 w-full bg-accent/5 blur-[2px]" />
      
      {/* Active Line */}
      <motion.div 
        className="absolute top-0 left-0 w-full bg-accent origin-top shadow-[0_0_20px_rgba(255,78,0,0.8)]"
        style={{ scaleY }}
      />
      
      {/* Moving Dot */}
      <motion.div 
        style={{ top: dotY }}
        className="absolute left-1/2 -translate-x-1/2 -translate-y-1/2 w-3.5 h-3.5 bg-accent rounded-full border-2 border-black shadow-[0_0_15px_rgba(255,78,0,1)] z-10"
      >
        <div className="absolute inset-0 bg-accent rounded-full animate-ping opacity-40" />
        <div className="absolute inset-[2px] bg-white rounded-full opacity-20" />
      </motion.div>
    </motion.div>
  );
};

/**
 * TextReveal Component
 * Animates text line by line or word by word.
 */
interface TextRevealProps {
  text: string;
  className?: string;
  delay?: number;
}

export const TextReveal: React.FC<TextRevealProps> = ({ text, className = "", delay = 0 }) => {
  const containerRef = useRef<HTMLElement>(null);
  const isInView = useInView(containerRef, { once: true, amount: 0.5 });
  
  const words = text.split(" ");
  
  const containerVariants: Variants = {
    hidden: { opacity: 0 },
    visible: (i = 1) => ({
      opacity: 1,
      transition: { staggerChildren: 0.08, delayChildren: delay * 0.1 },
    }),
  };
  
  const childVariants: Variants = {
    visible: {
      opacity: 1,
      y: 0,
      transition: {
        type: "spring",
        damping: 12,
        stiffness: 100,
      },
    },
    hidden: {
      opacity: 0,
      y: 40,
    },
  };
  
  return (
    <motion.span
      ref={containerRef as any}
      style={{ display: "inline-flex", flexWrap: "wrap", overflow: "hidden", justifyContent: "center" }}
      variants={containerVariants}
      initial="hidden"
      animate={isInView ? "visible" : "hidden"}
      className={className}
    >
      {words.map((word, index) => (
        <motion.span variants={childVariants} style={{ marginRight: "0.25em", display: "inline-block" }} key={index}>
          {word}
        </motion.span>
      ))}
    </motion.span>
  );
};

/**
 * Magnetic Component
 * Pulls elements towards the mouse using Framer Motion springs.
 */
interface MagneticProps {
  children: React.ReactElement;
  intensity?: number;
}

export const Magnetic: React.FC<MagneticProps> = ({ children, intensity = 0.2 }) => {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });

  const handleMouse = (e: React.MouseEvent<HTMLDivElement>) => {
    const { clientX, clientY } = e;
    if (ref.current) {
      const { height, width, left, top } = ref.current.getBoundingClientRect();
      const middleX = clientX - (left + width / 2);
      const middleY = clientY - (top + height / 2);
      setPosition({ x: middleX * intensity, y: middleY * intensity });
    }
  };

  const reset = () => {
    setPosition({ x: 0, y: 0 });
  };

  const { x, y } = position;

  return (
    <motion.div
      ref={ref}
      onMouseMove={handleMouse}
      onMouseLeave={reset}
      animate={{ x, y }}
      transition={{ type: "spring", stiffness: 150, damping: 15, mass: 0.1 }}
      className="inline-block"
    >
      {children}
    </motion.div>
  );
};
