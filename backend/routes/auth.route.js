import express from "express";
import bcrypt from "bcryptjs";
import db from "../db.js";
import generateOTP from "../utils/otp.js";
import {
    generateAccessToken,
    generateRefreshToken,
    verifyRefresh,
} from "../utils/jwt.js";

const router = express.Router();

router.post("/register", (req, res) => {
    const { email, password } = req.body;

    const otp = generateOTP();
    const expiresAt = Date.now() + 5 * 60 * 1000; // 5 min

    console.log("OTP for", email, "is:", otp);

    db.prepare("DELETE FROM otp WHERE email = ?").run(email);

    db.prepare(
        "INSERT INTO otp (email, otp, expiresAt) VALUES (?, ?, ?)"
    ).run(email, otp, expiresAt);

    res.json({ message: "OTP sent (check console)" });
});

router.post("/verify-otp", (req, res) => {
    const { email, otp, password } = req.body;

    const record = db
        .prepare("SELECT * FROM otp WHERE email = ?")
        .get(email);

    if (!record) return res.status(400).json({ message: "OTP not found" });

    if (record.otp !== otp)
        return res.status(400).json({ message: "Invalid OTP" });

    if (Date.now() > record.expiresAt)
        return res.status(400).json({ message: "OTP expired" });

    const hashed = bcrypt.hashSync(password, 10);

    db.prepare("INSERT INTO users (email, password) VALUES (?, ?)").run(
        email,
        hashed
    );

    db.prepare("DELETE FROM otp WHERE email = ?").run(email);

    res.json({ message: "User created successfully" });
});

router.post("/login", (req, res) => {
    const { email, password } = req.body;

    const user = db.prepare("SELECT * FROM users WHERE email = ?").get(email);

    if (!user) return res.status(400).json({ message: "User not found" });

    const valid = bcrypt.compareSync(password, user.password);

    if (!valid) return res.status(400).json({ message: "Wrong password" });

    const accessToken = generateAccessToken({ id: user.id, email: user.email });
    const refreshToken = generateRefreshToken({
        id: user.id,
        email: user.email,
    });

    db.prepare("UPDATE users SET refreshToken = ? WHERE id = ?").run(
        refreshToken,
        user.id
    );

    res.cookie("refreshToken", refreshToken, {
        httpOnly: true,
        secure: false,
    });

    res.json({ accessToken });
});

router.post("/refresh", (req, res) => {
    const token = req.cookies.refreshToken;

    if (!token) return res.status(401).json({ message: "No refresh token" });

    try {
        const decoded = verifyRefresh(token);

        const user = db
            .prepare("SELECT * FROM users WHERE id = ?")
            .get(decoded.id);

        if (user.refreshToken !== token) {
            return res.status(403).json({ message: "Invalid refresh token" });
        }

        const newAccessToken = generateAccessToken({
            id: user.id,
            email: user.email,
        });

        res.json({ accessToken: newAccessToken });
    } catch (err) {
        res.status(403).json({ message: "Token invalid" });
    }
});

router.post("/logout", (req, res) => {
    const token = req.cookies.refreshToken;

    if (token) {
        const decoded = verifyRefresh(token);

        db.prepare("UPDATE users SET refreshToken = NULL WHERE id = ?").run(
            decoded.id
        );
    }

    res.clearCookie("refreshToken");
    res.json({ message: "Logged out" });
});

export default router;