import jwt from "jsonwebtoken";
const { sign, verify } = jwt;

export function generateAccessToken(user) {
    return sign(user, process.env.ACCESS_TOKEN_SECRET, { expiresIn: "5h" });
}

export function generateRefreshToken(user) {
    return sign(user, process.env.REFRESH_TOKEN_SECRET, { expiresIn: "7d" });
}

export function verifyAccess(token) {
    return verify(token, process.env.ACCESS_TOKEN_SECRET);
}

export function verifyRefresh(token) {
    return verify(token, process.env.REFRESH_TOKEN_SECRET);
}