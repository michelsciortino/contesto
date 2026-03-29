import uuid
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Rule
from app.schemas import RuleCreate, RuleUpdate, RuleOut
from app.matcher import refresh_rules_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/rules", tags=["rules"])


async def _refresh_cache(db: AsyncSession) -> None:
    result = await db.execute(
        select(Rule).where(Rule.is_active == True).order_by(Rule.priority)
    )
    rules = result.scalars().all()
    try:
        await refresh_rules_cache([
            {
                "id": str(r.id),
                "name": r.name,
                "priority": r.priority,
                "match_logic": r.match_logic,
                "mutate_logic": r.mutate_logic,
            }
            for r in rules
        ])
    except Exception as e:
        logger.error(f"Redis cache refresh failed: {e}")


@router.get("", response_model=list[RuleOut])
async def list_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Rule).order_by(Rule.priority))
    return result.scalars().all()


@router.get("/{rule_id}", response_model=RuleOut)
async def get_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.post("", response_model=RuleOut, status_code=201)
async def create_rule(body: RuleCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Rule).where(Rule.priority == body.priority))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A rule with this priority already exists")
    rule = Rule(**body.model_dump())
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    await _refresh_cache(db)
    return rule


@router.put("/{rule_id}", response_model=RuleOut)
async def update_rule(rule_id: uuid.UUID, body: RuleUpdate, db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(rule, field, value)
    await db.commit()
    await db.refresh(rule)
    await _refresh_cache(db)
    return rule


@router.delete("/{rule_id}", response_model=RuleOut)
async def delete_rule(rule_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    rule = await db.get(Rule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule.is_active = False
    await db.commit()
    await db.refresh(rule)
    await _refresh_cache(db)
    return rule
