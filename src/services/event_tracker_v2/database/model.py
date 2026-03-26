from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, Index
from sqlalchemy.orm import relationship, DeclarativeMeta
from sqlalchemy.ext.declarative import declarative_base

Base: DeclarativeMeta = declarative_base()


class RankRecord(Base):
    __tablename__ = 'rank_record'

    id = Column(Integer, primary_key=True)
    score = Column(Integer, nullable=False)
    rank = Column(Integer, nullable=False) 
    user_record_id = Column(Integer, ForeignKey('user_record.id'), nullable=False)
    time_record_id = Column(Integer, ForeignKey('time_record.id'), nullable=False)

    user_record = relationship('UserRecord', back_populates='rankings')
    time_record = relationship('TimeRecord', back_populates='rankings')

    __table_args__ = (
        Index('idx_rank_user_id', 'user_record_id'),
        Index('idx_rank_time_id', 'time_record_id'),
        Index('idx_rank_id_desc', 'rank', 'id'),
        Index('idx_rank_time_record_id', 'rank', 'time_record_id'),
    )


class UserRecord(Base):
    __tablename__ = 'user_record'
    id = Column(Integer, primary_key=True)
    uid = Column(Integer, nullable=False, unique=True)
    name = Column(String, nullable=False)

    rankings = relationship('RankRecord', back_populates='user_record')


class TimeRecord(Base):
    __tablename__ = 'time_record'
    id = Column(Integer, primary_key=True)
    ts = Column(Integer, nullable=False, unique=True)

    rankings = relationship('RankRecord', back_populates='time_record')