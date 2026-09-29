package com.odh.auth.repository;

import com.odh.auth.domain.AppUser;
import com.odh.auth.domain.UserStatus;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface AppUserRepository extends JpaRepository<AppUser, Long> {
    Optional<AppUser> findByEmail(String email);

    /**
     * organization_id 를 raw SQL 대신 JPQL 벌크 업데이트로 바꾼 것.
     * {@code clearAutomatically = true} 가 영속성 컨텍스트를 비워, 실행 전에 이미
     * 조회돼 캐시에 올라 있던 대상 엔티티를 재조회 시 옛 값이 아니라 방금 바꾼 값으로 돌려준다.
     */
    @Modifying(clearAutomatically = true)
    @Query("UPDATE AppUser a SET a.organizationId = :organizationId, a.updatedAt = CURRENT_TIMESTAMP WHERE a.id = :id")
    void updateOrganization(@Param("id") Long id, @Param("organizationId") Long organizationId);

    /** 비밀번호 변경·재발급. 해시만 저장한다. */
    @Modifying(clearAutomatically = true)
    @Query("UPDATE AppUser a SET a.passwordHash = :passwordHash, a.updatedAt = CURRENT_TIMESTAMP WHERE a.id = :id")
    void updatePasswordHash(@Param("id") Long id, @Param("passwordHash") String passwordHash);

    /** 계정 활성/비활성 전환. */
    @Modifying(clearAutomatically = true)
    @Query("UPDATE AppUser a SET a.status = :status, a.updatedAt = CURRENT_TIMESTAMP WHERE a.id = :id")
    void updateStatus(@Param("id") Long id, @Param("status") UserStatus status);
}
