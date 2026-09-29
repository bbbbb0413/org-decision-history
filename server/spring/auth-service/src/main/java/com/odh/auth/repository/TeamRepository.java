package com.odh.auth.repository;

import com.odh.auth.domain.Team;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;

public interface TeamRepository extends JpaRepository<Team, Long> {

    /** 사용자가 소속된 팀 목록. user_team 연결 테이블을 거친다. */
    @Query(value = """
            SELECT t.* FROM team t
            JOIN user_team ut ON ut.team_id = t.id
            WHERE ut.user_id = :userId
            ORDER BY t.name
            """, nativeQuery = true)
    List<Team> findByUserId(Long userId);

    /** 조직의 팀 목록. 소속 편집 화면의 선택지로 쓴다. */
    List<Team> findByOrganizationIdOrderByName(Long organizationId);
}
