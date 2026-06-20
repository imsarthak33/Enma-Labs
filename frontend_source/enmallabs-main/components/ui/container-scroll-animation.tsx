"use client";
import React, { useRef } from "react";
import { useScroll, useTransform, motion, MotionValue, useSpring } from "framer-motion";

export const ContainerScroll = ({
  titleComponent,
  children,
}: {
  titleComponent: string | React.ReactNode;
  children: React.ReactNode;
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const { scrollYProgress } = useScroll({
    target: containerRef,
    offset: ["start end", "end start"],
  });
  
  // Since global smooth scrolling was removed, we absolutely need local physics
  // over the 3D scroll effect or it will tie rigidly to chunky mouse-wheel ticks.
  const smoothProgress = useSpring(scrollYProgress, {
    stiffness: 80,
    damping: 20,
    mass: 0.1
  });

  const [isMobile, setIsMobile] = React.useState(false);

  React.useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 768);
    };
    checkMobile();
    window.addEventListener("resize", checkMobile);
    return () => {
      window.removeEventListener("resize", checkMobile);
    };
  }, []);

  const scaleDimensions = () => {
    return isMobile ? [0.8, 1] : [1.1, 0.95];
  };

  // Increased sensitivity by finishing animation at 0.6 instead of 1
  const rotate = useTransform(smoothProgress, [0, 0.7], [35, 0]);
  const scale = useTransform(smoothProgress, [0, 0.7], scaleDimensions());
  
  // Header starts pushed down (behind the card) and translates up rapidly, revealing it
  const translate = useTransform(smoothProgress, [0, 0.7], [150, -50]);

  return (
    <div
      className="h-[60rem] md:h-[80rem] flex items-center justify-center relative p-2 md:p-20"
      ref={containerRef}
    >
      <div
        className="py-10 md:py-40 w-full relative"
        style={{
          perspective: "1200px",
        }}
      >
        <Header translate={translate} titleComponent={titleComponent} />
        <Card rotate={rotate} scale={scale}>
          {children}
        </Card>
      </div>
    </div>
  );
};

export const Header = ({ translate, titleComponent }: any) => {
  return (
    <motion.div
      style={{
        translateY: translate,
      }}
      className="div max-w-5xl mx-auto text-center"
    >
      {titleComponent}
    </motion.div>
  );
};

export const Card = ({
  rotate,
  scale,
  children,
}: {
  rotate: MotionValue<number>;
  scale: MotionValue<number>;
  children: React.ReactNode;
}) => {
  return (
    <motion.div
      style={{
        rotateX: rotate,
        scale,
        boxShadow:
          "0 0 #0000004d, 0 9px 20px #0000004a, 0 37px 37px #00000042, 0 84px 50px #00000026, 0 149px 60px #0000000a, 0 233px 65px #00000003",
      }}
      className="max-w-5xl -mt-12 mx-auto h-[35rem] md:h-[45rem] w-full border border-white/10 p-2 md:p-6 bg-black/50 backdrop-blur-3xl rounded-[30px] shadow-2xl overflow-hidden flex items-center justify-center"
    >
      <div className="h-full w-full overflow-hidden rounded-2xl bg-black/80 flex items-center justify-center md:rounded-2xl md:p-4">
        {children}
      </div>
    </motion.div>
  );
};
