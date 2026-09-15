"""收藏模块 —— 收藏/分组/PDF 附件的页面路由与 API。

一篇论文一条收藏记录（Favorite），可通过关联表同时归入多个分组
（FavoriteGroup）；PDF 附件支持两种方式：
  - 上传：文件复制到 data/attachments/，随项目数据持久化；
  - 本地路径：仅记录本机绝对路径，通过后端端点流式预览，不复制文件。
"""

import logging
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Query, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from pydantic import BaseModel
from sqlalchemy import func, desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Paper, Favorite, FavoriteGroup, favorite_group_link

logger = logging.getLogger(__name__)

router = APIRouter()

# 上传 PDF 的保存目录
ATTACHMENTS_DIR = Path(__file__).parent.parent / "data" / "attachments"
ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def _favorite_ids(db: Session) -> set:
    """全部已收藏的 paper_id 集合（列表/详情页渲染星标用）。"""
    return {pid for (pid,) in db.query(Favorite.paper_id).all()}


# ============================================================
# 页面路由
# ============================================================
@router.get("/favorites", response_class=HTMLResponse)
async def favorites_page(
    request: Request,
    db: Session = Depends(get_db),
    group: int = Query(0),  # 0=全部收藏, -1=未分组
    search: str = Query(""),
):
    """收藏管理页 —— 左侧分组栏 + 右侧收藏列表。"""
    query = db.query(Favorite).join(Favorite.paper)

    if group > 0:
        query = query.filter(Favorite.groups.any(FavoriteGroup.id == group))
    elif group == -1:
        query = query.filter(~Favorite.groups.any())

    if search:
        term = f"%{search}%"
        query = query.filter(
            (Paper.title.like(term)) | (Favorite.note.like(term))
        )

    favorites = query.order_by(desc(Paper.date_added)).all()

    groups = db.query(FavoriteGroup).order_by(FavoriteGroup.name).all()
    group_counts = dict(
        db.query(
            favorite_group_link.c.group_id, func.count(favorite_group_link.c.favorite_id)
        )
        .group_by(favorite_group_link.c.group_id)
        .all()
    )
    total_fav = db.query(Favorite).count()
    grouped_fav = (
        db.query(func.count(func.distinct(favorite_group_link.c.favorite_id))).scalar()
        or 0
    )

    return request.app.state.templates.TemplateResponse(
        request,
        "favorites.html",
        {
            "favorites": favorites,
            "groups": groups,
            "group_counts": group_counts,
            "total_fav": total_fav,
            "ungrouped_fav": total_fav - grouped_fav,
            "current_group": group,
            "search": search,
        },
    )


# ============================================================
# 收藏星标（HTMX 局部刷新）
# ============================================================
def _fav_button_response(request: Request, paper_id: int, faved: bool):
    if request.headers.get("HX-Request"):
        return request.app.state.templates.TemplateResponse(
            request,
            "partials/fav_button.html",
            {"paper_id": paper_id, "faved": faved},
        )
    return JSONResponse({"paper_id": paper_id, "faved": faved})


@router.post("/api/v1/favorites/{paper_id}/toggle")
async def api_toggle_favorite(
    paper_id: int, request: Request, db: Session = Depends(get_db)
):
    """收藏 / 取消收藏（toggle），HTMX 请求返回星标按钮片段。"""
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper:
        return JSONResponse({"error": "论文不存在"}, status_code=404)

    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if fav:
        db.delete(fav)
        db.commit()
        return _fav_button_response(request, paper_id, False)

    db.add(Favorite(paper_id=paper_id))
    db.commit()
    return _fav_button_response(request, paper_id, True)


class FavoriteUpdate(BaseModel):
    note: str | None = None
    group_ids: list[int] | None = None


# 注意：必须注册在 /{paper_id} 之前，否则 "count" 会被当作 paper_id 解析
@router.get("/api/v1/favorites/count")
async def api_favorites_count(db: Session = Depends(get_db)):
    """收藏总数（供导航徽标等使用）。"""
    return {"count": db.query(Favorite).count()}


@router.get("/api/v1/favorites/{paper_id}")
async def api_get_favorite(paper_id: int, db: Session = Depends(get_db)):
    """查询单个收藏的备注与分组归属。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav:
        return JSONResponse({"error": "该论文尚未收藏"}, status_code=404)
    return {
        "paper_id": paper_id,
        "note": fav.note,
        "group_ids": [g.id for g in fav.groups],
    }


@router.put("/api/v1/favorites/{paper_id}")
async def api_update_favorite(
    paper_id: int, body: FavoriteUpdate, db: Session = Depends(get_db)
):
    """更新收藏的备注与分组归属。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav:
        return JSONResponse({"error": "该论文尚未收藏"}, status_code=404)

    if body.note is not None:
        fav.note = body.note.strip() or None

    if body.group_ids is not None:
        groups = (
            db.query(FavoriteGroup)
            .filter(FavoriteGroup.id.in_(body.group_ids))
            .all()
        )
        fav.groups = groups

    db.commit()
    return {"status": "ok", "group_ids": [g.id for g in fav.groups], "note": fav.note}


@router.delete("/api/v1/favorites/{paper_id}")
async def api_delete_favorite(paper_id: int, db: Session = Depends(get_db)):
    """取消收藏。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if fav:
        db.delete(fav)
        db.commit()
    return {"status": "ok"}


# ============================================================
# 分组管理
# ============================================================
class GroupCreate(BaseModel):
    name: str


@router.post("/api/v1/favorites/groups")
async def api_create_group(body: GroupCreate, db: Session = Depends(get_db)):
    """新建收藏分组。"""
    name = body.name.strip()
    if not name:
        return JSONResponse({"error": "分组名不能为空"}, status_code=400)
    if db.query(FavoriteGroup).filter(FavoriteGroup.name == name).first():
        return JSONResponse({"error": "分组已存在"}, status_code=409)
    group = FavoriteGroup(name=name)
    db.add(group)
    db.commit()
    return {"status": "ok", "id": group.id, "name": group.name}


@router.put("/api/v1/favorites/groups/{group_id}")
async def api_rename_group(
    group_id: int, body: GroupCreate, db: Session = Depends(get_db)
):
    """重命名分组。"""
    group = db.query(FavoriteGroup).filter(FavoriteGroup.id == group_id).first()
    if not group:
        return JSONResponse({"error": "分组不存在"}, status_code=404)
    name = body.name.strip()
    if not name:
        return JSONResponse({"error": "分组名不能为空"}, status_code=400)
    exists = (
        db.query(FavoriteGroup)
        .filter(FavoriteGroup.name == name, FavoriteGroup.id != group_id)
        .first()
    )
    if exists:
        return JSONResponse({"error": "分组已存在"}, status_code=409)
    group.name = name
    db.commit()
    return {"status": "ok"}


@router.delete("/api/v1/favorites/groups/{group_id}")
async def api_delete_group(group_id: int, db: Session = Depends(get_db)):
    """删除分组（收藏本身保留，仅解除归属）。"""
    group = db.query(FavoriteGroup).filter(FavoriteGroup.id == group_id).first()
    if not group:
        return JSONResponse({"error": "分组不存在"}, status_code=404)
    db.delete(group)  # ORM 删除会同时清理关联表记录
    db.commit()
    return {"status": "ok"}


@router.post("/api/v1/favorites/{paper_id}/groups/{group_id}")
async def api_add_to_group(
    paper_id: int, group_id: int, db: Session = Depends(get_db)
):
    """把收藏加入分组。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    group = db.query(FavoriteGroup).filter(FavoriteGroup.id == group_id).first()
    if not fav or not group:
        return JSONResponse({"error": "收藏或分组不存在"}, status_code=404)
    if group not in fav.groups:
        fav.groups.append(group)
        db.commit()
    return {"status": "ok"}


@router.delete("/api/v1/favorites/{paper_id}/groups/{group_id}")
async def api_remove_from_group(
    paper_id: int, group_id: int, db: Session = Depends(get_db)
):
    """把收藏移出分组。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav:
        return JSONResponse({"error": "收藏不存在"}, status_code=404)
    fav.groups = [g for g in fav.groups if g.id != group_id]
    db.commit()
    return {"status": "ok"}


# ============================================================
# PDF 附件
# ============================================================
def _resolve_pdf(fav: Favorite) -> Path | None:
    """把收藏的 pdf_path 解析为实际可访问的文件路径。"""
    if not fav.pdf_path:
        return None
    if fav.pdf_origin == "upload":
        return ATTACHMENTS_DIR / fav.pdf_path
    return Path(fav.pdf_path)


def _set_pdf(fav: Favorite, resolved: Path, origin: str, display_name: str):
    """替换附件前先清理旧的上传文件（本地路径方式不删原文件）。"""
    if fav.pdf_origin == "upload" and fav.pdf_path:
        old = ATTACHMENTS_DIR / fav.pdf_path
        if old.exists():
            try:
                old.unlink()
            except OSError as e:
                logger.warning(f"旧附件清理失败: {e}")
    fav.pdf_path = str(resolved) if origin == "local" else resolved.name
    fav.pdf_origin = origin
    fav.pdf_name = display_name


@router.post("/api/v1/favorites/{paper_id}/pdf")
async def api_upload_pdf(
    paper_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    """上传 PDF 并挂到收藏上（复制保存到 data/attachments/）。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav:
        return JSONResponse({"error": "该论文尚未收藏"}, status_code=404)

    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "只能上传 PDF 文件"}, status_code=400)

    # 避免文件名冲突/路径穿越：固定用自生成的存储名
    safe_stem = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", Path(filename).stem)[:60] or "pdf"
    stored_name = f"{paper_id}_{int(time.time())}_{safe_stem}.pdf"
    dest = ATTACHMENTS_DIR / stored_name

    size = 0
    with dest.open("wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            f.write(chunk)
    await file.close()

    if size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "上传文件为空"}, status_code=400)

    _set_pdf(fav, dest, "upload", filename)
    db.commit()
    return {"status": "ok", "pdf_name": fav.pdf_name, "pdf_origin": fav.pdf_origin}


class PdfPathSet(BaseModel):
    path: str


@router.put("/api/v1/favorites/{paper_id}/pdf")
async def api_set_pdf_path(
    paper_id: int, body: PdfPathSet, db: Session = Depends(get_db)
):
    """把本机已有 PDF 的路径挂到收藏上（不复制文件）。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav:
        return JSONResponse({"error": "该论文尚未收藏"}, status_code=404)

    raw = body.path.strip().strip('"')
    p = Path(raw)
    if not p.exists():
        return JSONResponse({"error": "文件不存在，请检查路径"}, status_code=400)
    if p.suffix.lower() != ".pdf":
        return JSONResponse({"error": "不是 PDF 文件"}, status_code=400)

    _set_pdf(fav, p, "local", p.name)
    db.commit()
    return {"status": "ok", "pdf_name": fav.pdf_name, "pdf_origin": fav.pdf_origin}


@router.delete("/api/v1/favorites/{paper_id}/pdf")
async def api_delete_pdf(paper_id: int, db: Session = Depends(get_db)):
    """移除 PDF 附件（上传的文件一并删除，本地路径方式只解除关联）。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav or not fav.pdf_path:
        return JSONResponse({"error": "没有附件"}, status_code=404)

    if fav.pdf_origin == "upload":
        stored = ATTACHMENTS_DIR / fav.pdf_path
        if stored.exists():
            try:
                stored.unlink()
            except OSError as e:
                logger.warning(f"附件删除失败: {e}")

    fav.pdf_path = None
    fav.pdf_origin = None
    fav.pdf_name = None
    db.commit()
    return {"status": "ok"}


@router.get("/api/v1/favorites/{paper_id}/pdf")
async def api_preview_pdf(paper_id: int, db: Session = Depends(get_db)):
    """在浏览器中预览收藏的 PDF（新标签页打开）。"""
    fav = db.query(Favorite).filter(Favorite.paper_id == paper_id).first()
    if not fav or not fav.pdf_path:
        return JSONResponse({"error": "没有附件"}, status_code=404)

    p = _resolve_pdf(fav)
    if not p or not p.exists():
        return JSONResponse(
            {"error": "附件文件不存在（可能已被移动或删除）"}, status_code=404
        )

    return FileResponse(str(p), media_type="application/pdf", filename=fav.pdf_name)
