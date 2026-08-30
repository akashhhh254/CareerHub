import express, { Request, Response, NextFunction } from "express";
import session from "express-session";
import cookieParser from "cookie-parser";
import bcrypt from "bcryptjs";
import multer from "multer";
import path from "path";
import { fileURLToPath } from "url";
import dotenv from "dotenv";
import { GoogleGenAI, Type } from "@google/genai";
import pdfParse from "pdf-parse";
import mammoth from "mammoth";

dotenv.config();

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;

// ---------- In-Memory Database ----------
interface User {
  id: number;
  username: string;
  email: string;
  passwordHash: string;
}

interface Report {
  id: number;
  userId: number;
  resumeText: string;
  userGoal: string;
  result: any;
  createdAt: Date;
}

const users: User[] = [];
const reports: Report[] = [];
let nextUserId = 1;
let nextReportId = 1;

// Flash messages session helper
interface FlashMessage {
  category: "success" | "error" | "info";
  text: string;
}

declare module "express-session" {
  interface SessionData {
    user?: string;
    flashMessages?: FlashMessage[];
  }
}

// ---------- Express App Configuration ----------
app.set("view engine", "ejs");
app.set("views", path.join(__dirname, "views"));

// Static files: serve /static and root
app.use("/static", express.static(path.join(__dirname, "static")));
app.use(express.static(path.join(__dirname, "static")));

app.use(express.urlencoded({ extended: true, limit: "10mb" }));
app.use(express.json({ limit: "10mb" }));
app.use(cookieParser());
app.use(
  session({
    secret: process.env.SESSION_SECRET || process.env.SECRET_KEY || "career-hub-secret-key-2026",
    resave: false,
    saveUninitialized: false,
    cookie: { maxAge: 24 * 60 * 60 * 1000 },
  })
);

// Middleware to inject user and flash messages into all views
app.use((req: Request, res: Response, next: NextFunction) => {
  res.locals.user = req.session.user || null;
  res.locals.messages = req.session.flashMessages || [];
  req.session.flashMessages = [];
  next();
});

function flash(req: Request, category: "success" | "error" | "info", text: string) {
  if (!req.session.flashMessages) {
    req.session.flashMessages = [];
  }
  req.session.flashMessages.push({ category, text });
}

function requireAuth(req: Request, res: Response, next: NextFunction) {
  if (!req.session.user) {
    return res.redirect("/login");
  }
  next();
}

// ---------- File Upload Configuration ----------
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 5 * 1024 * 1024 }, // 5 MB limit
  fileFilter: (_req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    if (ext === ".pdf" || ext === ".docx") {
      cb(null, true);
    } else {
      cb(new Error("Unsupported file type. Please upload a PDF or DOCX file."));
    }
  },
});

// ---------- AI Resume Analyzer (Gemini SDK) ----------
function getGeminiClient(): GoogleGenAI | null {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    return null;
  }
  return new GoogleGenAI({
    apiKey,
    httpOptions: {
      headers: {
        "User-Agent": "aistudio-build",
      },
    },
  });
}

const RESUME_SCHEMA = {
  type: Type.OBJECT,
  properties: {
    resume_score: {
      type: Type.INTEGER,
      description: "Overall resume quality score out of 100, considering clarity, relevance to the goal, and completeness.",
    },
    skills: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Relevant skills detected in the resume.",
    },
    missing_skills: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Important skills for the target role that are missing from the resume.",
    },
    strengths: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "What the resume does well.",
    },
    weaknesses: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Weaknesses or gaps in the resume.",
    },
    improvement_suggestions: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Concrete, actionable suggestions to improve the resume.",
    },
    recommended_roles: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Job titles/roles this resume is a good fit for, given the goal.",
    },
    roadmap: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Step-by-step learning roadmap to close the skill gaps for the goal.",
    },
    interview_questions: {
      type: Type.ARRAY,
      items: { type: Type.STRING },
      description: "Likely interview questions for the target role, based on the resume.",
    },
    overall_feedback: {
      type: Type.STRING,
      description: "A short (3-5 sentence) overall summary of the analysis.",
    },
  },
  required: [
    "resume_score",
    "skills",
    "missing_skills",
    "strengths",
    "weaknesses",
    "improvement_suggestions",
    "recommended_roles",
    "roadmap",
    "interview_questions",
    "overall_feedback",
  ],
};

async function analyzeResume(resumeText: string, userGoal: string): Promise<any> {
  if (!resumeText || !resumeText.trim()) {
    return { error: "No resume text was found to analyze." };
  }

  const ai = getGeminiClient();
  if (!ai) {
    return {
      error:
        "Resume analysis is unavailable right now because the AI service is not configured. " +
        "Set GEMINI_API_KEY in your environment to enable analysis.",
    };
  }

  const prompt = `
You are a senior software engineer and hiring manager reviewing a resume.

User's career goal: "${userGoal}"

Evaluate the resume strictly based on this goal:
- Extract only skills that are actually present in the resume text.
- Ignore skills that are irrelevant to the goal (e.g. do not list Excel as a key skill for a backend engineering goal).
- Identify real, specific gaps between the resume and the goal.
- Give a resume_score out of 100 that reflects overall quality AND fit for the goal.
- Keep every list concise (roughly 3-8 items) and specific to this resume and goal.
- Base everything only on the resume text provided below. Do not invent experience or credentials that are not present in the text.

Resume:
"""
${resumeText}
"""
`;

  try {
    const response = await ai.models.generateContent({
      model: "gemini-3.7-flash",
      contents: prompt,
      config: {
        responseMimeType: "application/json",
        responseSchema: RESUME_SCHEMA,
      },
    });

    const text = response.text;
    if (!text) {
      return { error: "Empty response received from AI model." };
    }
    return JSON.parse(text);
  } catch (error: any) {
    console.error("Gemini API Error:", error);
    return {
      error: `Error analyzing resume: ${error?.message || "Failed to analyze resume"}`,
    };
  }
}

// ---------- Routes ----------

// Home
app.get("/", (req: Request, res: Response) => {
  if (req.session.user) {
    return res.redirect("/dashboard");
  }
  return res.redirect("/login");
});

// Sign Up
app.get("/signup", (req: Request, res: Response) => {
  res.render("signup");
});

app.post("/signup", async (req: Request, res: Response) => {
  const email = (req.body.email || "").trim().toLowerCase();
  const username = (req.body.username || "").trim();
  const password = req.body.password || "";

  if (!email || !username || !password) {
    flash(req, "error", "Please fill in username, email and password.");
    return res.render("signup");
  }

  if (password.length < 6) {
    flash(req, "error", "Password must be at least 6 characters long.");
    return res.render("signup");
  }

  const existing = users.find((u) => u.email === email || u.username === username);
  if (existing) {
    flash(req, "error", "An account with that email or username already exists.");
    return res.render("signup");
  }

  const passwordHash = await bcrypt.hash(password, 10);
  users.push({
    id: nextUserId++,
    username,
    email,
    passwordHash,
  });

  flash(req, "success", "Account created! Please log in.");
  return res.redirect("/login");
});

// Login
app.get("/login", (req: Request, res: Response) => {
  res.render("login");
});

app.post("/login", async (req: Request, res: Response) => {
  const email = (req.body.email || "").trim().toLowerCase();
  const password = req.body.password || "";

  const user = users.find((u) => u.email === email);
  if (user && (await bcrypt.compare(password, user.passwordHash))) {
    req.session.user = user.username;
    return res.redirect("/dashboard");
  }

  flash(req, "error", "Invalid email or password.");
  return res.render("login");
});

// Forgot Password
app.get("/forget", (req: Request, res: Response) => {
  res.render("forget");
});

app.post("/forget", (req: Request, res: Response) => {
  flash(
    req,
    "success",
    "If an account exists for that email, a reset link would be sent. (Email sending isn't configured yet in this project.)"
  );
  return res.redirect("/login");
});

// Dashboard
app.get("/dashboard", requireAuth, (req: Request, res: Response) => {
  res.render("dashboard", { result: null, resume_text: "", user_goal: "" });
});

app.post(
  "/dashboard",
  requireAuth,
  upload.single("resume_file"),
  async (req: Request, res: Response) => {
    let userGoal = (req.body.desired_role || "").trim();
    let resumeText = (req.body.resume || "").trim();
    let result: any = null;

    if (req.file) {
      const ext = path.extname(req.file.originalname).toLowerCase();
      try {
        if (ext === ".pdf") {
          const parsed = await pdfParse(req.file.buffer);
          resumeText = (parsed.text || "").trim();
        } else if (ext === ".docx") {
          const docResult = await mammoth.extractRawText({ buffer: req.file.buffer });
          resumeText = (docResult.value || "").trim();
        }
      } catch (err: any) {
        result = { error: `Error reading file: ${err?.message || "Invalid file"}` };
      }
    }

    if (!result) {
      if (!resumeText) {
        result = { error: "Please paste your resume text or upload a PDF/DOCX file." };
      } else if (!userGoal) {
        result = { error: "Please tell us the role/goal you're aiming for." };
      } else {
        result = await analyzeResume(resumeText, userGoal);

        if (!result.error) {
          const currentUser = users.find((u) => u.username === req.session.user);
          if (currentUser) {
            reports.push({
              id: nextReportId++,
              userId: currentUser.id,
              resumeText,
              userGoal,
              result,
              createdAt: new Date(),
            });
          }
        }
      }
    }

    res.render("dashboard", {
      result,
      resume_text: resumeText,
      user_goal: userGoal,
    });
  }
);

// History
app.get("/history", requireAuth, (req: Request, res: Response) => {
  const currentUser = users.find((u) => u.username === req.session.user);
  let userReports: any[] = [];

  if (currentUser) {
    const rawReports = reports
      .filter((r) => r.userId === currentUser.id)
      .sort((a, b) => b.id - a.id);

    userReports = rawReports.map((r) => ({
      resume_text: r.resumeText,
      user_goal: r.userGoal,
      result: r.result,
      created_at: r.createdAt,
    }));
  }

  res.render("history", { reports: userReports });
});

// Logout
app.get("/logout", (req: Request, res: Response) => {
  req.session.destroy(() => {
    res.redirect("/login");
  });
});

// Start Server
app.listen(PORT, "0.0.0.0", () => {
  console.log(`Career Hub server running on http://0.0.0.0:${PORT}`);
});
